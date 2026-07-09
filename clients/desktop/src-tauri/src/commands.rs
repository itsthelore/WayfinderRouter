//! The webview's Rust command surface (WF-ADR-0042 §3: minimal + auditable). The webview talks
//! to the gateway itself over loopback fetch; Rust only does what a webview can't — drive the
//! tray, the service lifecycle, and open fixed local targets. No arbitrary shell or open: every
//! command maps to a fixed action.

use std::process::Command;

use tauri::image::Image;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

use crate::keychain;
use crate::service::{self, ServiceAction};

const TRAY_RUNNING: &[u8] = include_bytes!("../icons/tray-running-Template@2x.png");
const TRAY_DEGRADED: &[u8] = include_bytes!("../icons/tray-degraded-Template@2x.png");
const TRAY_STOPPED: &[u8] = include_bytes!("../icons/tray-stopped-Template@2x.png");

pub fn tray_image(state: &str) -> Image<'static> {
    let bytes = match state {
        "running" => TRAY_RUNNING,
        "degraded" => TRAY_DEGRADED,
        _ => TRAY_STOPPED,
    };
    // The bytes are generated + committed, so a decode failure is a build error, not runtime.
    Image::from_bytes(bytes).expect("tray template PNG decodes")
}

/// The live meter W (glance pivot): the hollow W with its bottom `fill` fraction solid — the
/// menu-bar icon itself is a usage meter (fill = local-routing share; the savings `$` rides in
/// the title). Row-spliced from the two committed templates, so the result stays a pure
/// black+alpha template image. A visual floor keeps a running-but-0% W visibly distinct from
/// the hollow "stopped" W.
const METER_FLOOR: f64 = 0.12;

fn meter_image(fill: f64) -> Image<'static> {
    let solid = tray_image("running");
    let hollow = tray_image("stopped");
    let (w, h) = (solid.width(), solid.height());
    debug_assert_eq!((w, h), (hollow.width(), hollow.height()));
    let fill = fill.clamp(METER_FLOOR, 1.0);
    // First row (from the top) that renders solid; everything above stays hollow.
    let cutoff = ((1.0 - fill) * h as f64).round() as u32;
    let stride = (w * 4) as usize;
    let mut rgba = Vec::with_capacity(stride * h as usize);
    for y in 0..h {
        let src = if y >= cutoff { solid.rgba() } else { hollow.rgba() };
        let row = y as usize * stride;
        rgba.extend_from_slice(&src[row..row + stride]);
    }
    Image::new_owned(rgba, w, h)
}

/// Drive the tray from the webview's single healthz poll: the W shape carries health
/// (running/degraded/stopped), `fill` carries the local-routing share when running (the live
/// meter), and the title carries the savings `$` only — never a route (WF-DESIGN-0012 + the
/// glance amendment). Health outranks the meter: degraded keeps its notch, stopped stays hollow.
#[tauri::command]
pub fn set_tray_state(
    app: AppHandle,
    state: String,
    title: Option<String>,
    fill: Option<f64>,
) -> Result<(), String> {
    let tray = app.tray_by_id("wayfinder").ok_or("tray not found")?;
    let icon = match (state.as_str(), fill) {
        ("running", Some(f)) if f.is_finite() => meter_image(f),
        _ => tray_image(&state),
    };
    tray.set_icon(Some(icon)).map_err(|e| e.to_string())?;
    tray.set_icon_as_template(true).map_err(|e| e.to_string())?;
    // Empty/None title clears it — the tray shows only a savings figure, never a route.
    tray.set_title(title.filter(|t| !t.is_empty())).map_err(|e| e.to_string())?;
    Ok(())
}

/// install | uninstall | start | stop — the service-first lifecycle (WF-ADR-0038/0042). The
/// exact argv lives in `service::argv`; this only validates the action and surfaces the result.
#[tauri::command]
pub fn service_control(action: String) -> Result<String, String> {
    let action = ServiceAction::parse(&action).ok_or_else(|| format!("unknown action: {action}"))?;
    service::run(action)
}

/// Open one of three fixed targets in the default handler — never a webview-supplied path.
#[tauri::command]
pub fn open_target(target: String) -> Result<(), String> {
    open_internal(&target)
}

/// Open the Settings window (WF-DESIGN-0014): a real, resizable, decorated window — never an
/// in-popover slide-over. Built on demand rather than declared in `tauri.conf.json` so the
/// first click creates it and a later click after the user closed it just creates it again;
/// while it's alive we only show + focus the existing one. `section` (whitelisted) deep-links a
/// sidebar section on CREATION only — an already-open window is focused, not re-routed
/// (WF-DESIGN-0015, accepted limitation).
#[tauri::command]
pub fn open_settings(app: AppHandle, section: Option<String>) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("settings") {
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())?;
        return Ok(());
    }
    let mut url = String::from("index.html?window=settings");
    if let Some(s) = section {
        if !matches!(s.as_str(), "general" | "gateway" | "keys" | "privacy") {
            return Err(format!("unknown settings section: {s}"));
        }
        url.push_str(&format!("&section={s}"));
    }
    WebviewWindowBuilder::new(&app, "settings", WebviewUrl::App(url.into()))
        .title("Wayfinder Settings")
        .inner_size(900.0, 620.0)
        .min_inner_size(760.0, 480.0)
        .resizable(true)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// The footer's "Quit Wayfinder" row — the same exit the tray's own `PredefinedMenuItem::quit`
/// reaches, just callable from the webview (WF-DESIGN-0014).
#[tauri::command]
pub fn quit_app(app: AppHandle) {
    app.exit(0);
}

/// First-run scaffold (WF-ADR-0044 / WF-DESIGN-0015): shell the gateway's own `init --preset
/// <p> --keychain --path <shared config>` — the app authors zero TOML — then (re)install the
/// service so the unit carries `--config <shared config>`. The uninstall step is load-bearing:
/// re-installing over a loaded agent leaves launchd's OLD job spec running (bootstrap fails,
/// legacy load no-ops, the probe passes), so new ProgramArguments only apply across an
/// uninstall/install cycle. Best-effort on first run (nothing to uninstall).
#[tauri::command]
pub fn scaffold_config(preset: String) -> Result<String, String> {
    if !matches!(preset.as_str(), "hybrid" | "openai" | "gemini") {
        return Err(format!("unknown preset: {preset}"));
    }
    let home = std::env::var("HOME").unwrap_or_default();
    let path = std::env::var("PATH").unwrap_or_default();
    let wf = service::resolve_wayfinder(&home, &path).ok_or_else(|| {
        "couldn't find `wayfinder-router` — install the gateway first (pip install wayfinder-router)"
            .to_string()
    })?;
    let config = service::desktop_config_path(&home);
    if let Some(dir) = std::path::Path::new(&config).parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    }
    let out = Command::new(&wf)
        .args(["init", "--preset", &preset, "--keychain", "--path", &config])
        .output()
        .map_err(|e| format!("{wf}: {e}"))?;
    // "already exists" is EXIT_USAGE — idempotent re-entry after a partial first run keeps the
    // user's existing file (never --force from the app) and just proceeds to the install step.
    if !out.status.success() && !std::path::Path::new(&config).is_file() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!("init failed: {}", stderr.trim()));
    }
    let _ = service::run(service::ServiceAction::Uninstall); // best-effort, see doc comment
    service::run(service::ServiceAction::Install)?;
    Ok(format!("scaffolded {config} (preset: {preset}) and installed the service"))
}

/// Store a provider key in the macOS Keychain (WF-ADR-0044: stdin, never argv), then restart
/// the gateway — `resolve_keys` runs `api_key_cmd` only at startup, so the kickstart is what
/// makes the key take effect. The key passes through Rust transiently and never lands in a
/// file, argv, or JS state beyond the controlled input (WF-ADR-0004).
#[tauri::command]
pub fn store_provider_key(env_var: String, key: String) -> Result<String, String> {
    let line = keychain::keychain_script(keychain::KeyOp::Add, &env_var, &key)?;
    keychain::run_security(&line)?;
    service::run(service::ServiceAction::Start)?; // kickstart -k = restart -> keys re-resolve
    Ok(format!("stored {env_var} in the Keychain and restarted the gateway"))
}

/// Remove a provider key from the Keychain, then restart the gateway so /healthz reflects it.
#[tauri::command]
pub fn delete_provider_key(env_var: String) -> Result<String, String> {
    let line = keychain::keychain_script(keychain::KeyOp::Delete, &env_var, "")?;
    keychain::run_security(&line)?;
    service::run(service::ServiceAction::Start)?;
    Ok(format!("removed {env_var} from the Keychain and restarted the gateway"))
}

/// Rebind the popover toggle (WF-DESIGN-0015): whitelist-validated id, unregister-all, then
/// re-register with the shared toggle handler. Errors propagate so the Settings select can roll
/// back. Rust holds no persistent shortcut state — the webview's settings are the source of
/// truth and re-apply on every popover mount.
#[tauri::command]
pub fn set_shortcut(app: AppHandle, id: String) -> Result<(), String> {
    crate::apply_shortcut(&app, &id)
}

/// Flip GLOBAL offline-first delivery (WF-ADR-0039) for every client of the gateway — not just
/// this app's chat — by shelling the seam's mutation verb (WF-ADR-0044):
/// `config set gateway.offline true|false --path <shared config>`. The gateway hot-reloads the
/// change on its next request, so the popover's next healthz poll reflects it; no restart. The
/// CLI itself validates the edit against the real schema before writing.
#[tauri::command]
pub fn set_offline(on: bool) -> Result<String, String> {
    let home = std::env::var("HOME").unwrap_or_default();
    let path = std::env::var("PATH").unwrap_or_default();
    let wf = service::resolve_wayfinder(&home, &path).ok_or_else(|| {
        "couldn't find `wayfinder-router` — install the gateway first (pip install wayfinder-router)"
            .to_string()
    })?;
    let config = service::desktop_config_path(&home);
    let value = if on { "true" } else { "false" };
    let out = Command::new(&wf)
        .args(["config", "set", "gateway.offline", value, "--path", &config])
        .output()
        .map_err(|e| format!("{wf}: {e}"))?;
    if out.status.success() {
        Ok(format!("gateway.offline = {value}"))
    } else {
        let stderr = String::from_utf8_lossy(&out.stderr);
        Err(format!("config set failed: {}", stderr.trim()))
    }
}

/// A transition-edge notification (WF-DESIGN-0012: edge-only, off by default — the webview's
/// edge detector decides when). Dep-free via `osascript` so v1 pulls in no notification plugin;
/// app-attributed notifications (tauri-plugin-notification) are a follow-up pending a dependency
/// decision. `title`/`body` are passed as one `-e` arg (no shell), with AppleScript quotes escaped.
#[tauri::command]
pub fn notify(title: String, body: String) -> Result<(), String> {
    let esc = |s: &str| s.replace('\\', "\\\\").replace('"', "\\\"");
    let script = format!(
        "display notification \"{}\" with title \"{}\"",
        esc(&body),
        esc(&title)
    );
    let status = Command::new("osascript")
        .arg("-e")
        .arg(script)
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err("osascript notify failed".into())
    }
}

/// Shared by the command and the tray menu. Only the three known targets resolve to a path/URL.
pub fn open_internal(target: &str) -> Result<(), String> {
    let arg = match target {
        "dashboard" => "http://127.0.0.1:8088/router".to_string(),
        "config" => ensure_config_dir(),
        "logs" => logs_dir(),
        other => return Err(format!("unknown target: {other}")),
    };
    let status = Command::new("open").arg(&arg).status().map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("open {arg} failed"))
    }
}

fn home() -> String {
    std::env::var("HOME").unwrap_or_default()
}

/// The app owns `~/Library/Application Support/Wayfinder/` (WF-ADR-0042 §7); create it so "open
/// config" always lands somewhere real even before onboarding writes the toml.
fn ensure_config_dir() -> String {
    let dir = format!("{}/Library/Application Support/Wayfinder", home());
    std::fs::create_dir_all(&dir).ok();
    dir
}

fn logs_dir() -> String {
    format!("{}/Library/Logs", home())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn meter_full_is_the_solid_w() {
        assert_eq!(meter_image(1.0).rgba(), tray_image("running").rgba());
    }

    #[test]
    fn meter_floor_keeps_a_solid_sliver() {
        // fill 0 clamps to METER_FLOOR: the top is hollow, the bottom rows are solid — a
        // running-but-empty meter never renders identical to the hollow "stopped" W.
        let img = meter_image(0.0);
        let hollow = tray_image("stopped");
        let solid = tray_image("running");
        let stride = (img.width() * 4) as usize;
        let h = img.height() as usize;
        let cutoff = ((1.0 - METER_FLOOR) * h as f64).round() as usize;
        assert_eq!(&img.rgba()[..cutoff * stride], &hollow.rgba()[..cutoff * stride]);
        assert_eq!(&img.rgba()[cutoff * stride..], &solid.rgba()[cutoff * stride..]);
        assert_ne!(img.rgba(), hollow.rgba());
    }

    #[test]
    fn meter_half_splices_at_the_middle_row() {
        let img = meter_image(0.5);
        let hollow = tray_image("stopped");
        let solid = tray_image("running");
        let stride = (img.width() * 4) as usize;
        let cutoff = (img.height() as usize).div_ceil(2); // (1 - 0.5) * h rounded
        assert_eq!(&img.rgba()[..cutoff * stride], &hollow.rgba()[..cutoff * stride]);
        assert_eq!(&img.rgba()[cutoff * stride..], &solid.rgba()[cutoff * stride..]);
    }

    #[test]
    fn meter_stays_a_template_image() {
        // Template = black + alpha only: every RGB byte is 0 in both sources and the splice.
        let img = meter_image(0.62);
        for px in img.rgba().chunks_exact(4) {
            assert_eq!(&px[..3], &[0, 0, 0]);
        }
    }
}
