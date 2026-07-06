//! The webview's Rust command surface (WF-ADR-0042 §3: minimal + auditable). The webview talks
//! to the gateway itself over loopback fetch; Rust only does what a webview can't — drive the
//! tray, the service lifecycle, and open fixed local targets. No arbitrary shell or open: every
//! command maps to a fixed action.

use std::process::Command;

use tauri::image::Image;
use tauri::AppHandle;

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

/// Drive the tray from the webview's single healthz poll: the W shape carries health
/// (running/degraded/stopped) and the title carries the savings `$` only (WF-DESIGN-0012).
#[tauri::command]
pub fn set_tray_state(app: AppHandle, state: String, title: Option<String>) -> Result<(), String> {
    let tray = app.tray_by_id("wayfinder").ok_or("tray not found")?;
    tray.set_icon(Some(tray_image(&state))).map_err(|e| e.to_string())?;
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
