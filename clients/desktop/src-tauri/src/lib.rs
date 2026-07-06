//! Wayfinder Desktop — the Tauri v2 macOS menu-bar shell (WF-ADR-0042).
//!
//! A no-Dock accessory app: the signpost tray icon and the ⌥W hotkey both summon one
//! borderless, vibrant popover anchored under the tray icon — an ordinary macOS menu extra,
//! not a launcher (hide-on-blur, state preserved). The webview talks to the gateway directly
//! over loopback HTTP (not Rust IPC); Rust only drives the window, the tray, the
//! service-first lifecycle (WF-ROADMAP-0009 Phase 3), and the separate Settings window
//! (WF-DESIGN-0014). The gateway process is owned by the WF-ADR-0038 launchd agent — this app
//! never spawns it, only detects/attaches and offers service control.

mod commands;
mod keychain;
mod service;
mod tray;

use tauri::{App, AppHandle, Manager, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_positioner::{Position, WindowExt};

const POPOVER: &str = "popover";

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // single-instance must be registered FIRST so a second launch just resurfaces us
        // instead of spinning up a second tray.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_popover(app);
        }))
        .plugin(tauri_plugin_positioner::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_log::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            commands::set_tray_state,
            commands::service_control,
            commands::open_target,
            commands::open_settings,
            commands::quit_app,
            commands::notify,
            commands::scaffold_config,
            commands::store_provider_key,
            commands::delete_provider_key,
            commands::set_shortcut,
            commands::set_offline,
            commands::add_model,
            commands::detect_local_providers,
        ])
        .setup(|app| {
            setup(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Hide-on-blur: dismiss the popover to the tray. It is hidden, not destroyed, so
            // the React draft survives the next open (Esc in the webview also just hides it).
            // The Settings window is a real window — it closes (and is torn down) normally.
            if window.label() == POPOVER {
                if let WindowEvent::Focused(false) = event {
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running the Wayfinder desktop app");
}

fn setup(app: &mut App) -> Result<(), Box<dyn std::error::Error>> {
    // No Dock icon: a menu-bar-only accessory app. The Settings window still opens fine without
    // one — the same posture most menu-bar utilities take for their preferences window.
    #[cfg(target_os = "macos")]
    app.set_activation_policy(tauri::ActivationPolicy::Accessory);

    // Vibrant popover backdrop (NSVisualEffectView). Requires macOSPrivateApi + a transparent
    // window; the webview keeps a transparent body so the material shows through. The 13px
    // material radius matches `#root { border-radius: 13px }` in globals.css so the CSS and
    // material corners coincide (WF-DESIGN-0012). Best-effort: if the private API is
    // unavailable we fall back to the webview's own background.
    #[cfg(target_os = "macos")]
    if let Some(win) = app.get_webview_window(POPOVER) {
        use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial, NSVisualEffectState};
        let _ = apply_vibrancy(
            &win,
            NSVisualEffectMaterial::Popover,
            Some(NSVisualEffectState::Active),
            Some(13.0),
        );
    }

    tray::build(app)?;
    // Best-effort: registration can fail if another app claimed the combo (RegisterEventHotKey
    // needs no Accessibility grant — the old warning here claimed otherwise and was wrong, per
    // WF-ROADMAP-0009 Phase 4's note); the popover still opens from the tray, and the Settings
    // shortcut row lets the user pick a different combo.
    if let Err(e) = apply_shortcut(app.handle(), DEFAULT_SHORTCUT) {
        log::warn!("global shortcut unavailable (combo already claimed?): {e}");
    }
    Ok(())
}

/// The rebind whitelist (WF-DESIGN-0015): fixed ids, fixed combos — the webview can never
/// register an arbitrary key. No ⌥Space: it collides with common launchers (the roadmap's own
/// note). Ids are what `lib/settings.ts` persists.
pub(crate) const DEFAULT_SHORTCUT: &str = "alt+w";

fn shortcut_for(id: &str) -> Option<Shortcut> {
    match id {
        "alt+w" => Some(Shortcut::new(Some(Modifiers::ALT), Code::KeyW)),
        "alt+shift+w" => Some(Shortcut::new(Some(Modifiers::ALT | Modifiers::SHIFT), Code::KeyW)),
        "ctrl+alt+w" => Some(Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyW)),
        "cmd+shift+w" => Some(Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyW)),
        _ => None,
    }
}

/// (Re)bind the popover toggle: validate against the whitelist, drop every prior registration,
/// register the new combo with the one shared handler. Called at setup (default) and from the
/// `set_shortcut` command (Settings). Errors propagate so the caller can roll back its UI.
pub(crate) fn apply_shortcut(app: &AppHandle, id: &str) -> Result<(), String> {
    let shortcut = shortcut_for(id).ok_or_else(|| format!("unknown shortcut: {id}"))?;
    let gs = app.global_shortcut();
    gs.unregister_all().map_err(|e| e.to_string())?;
    gs.on_shortcut(shortcut, |app, _shortcut, event| {
        if event.state == ShortcutState::Pressed {
            toggle_popover(app);
        }
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub(crate) fn toggle_popover(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(POPOVER) {
        if win.is_visible().unwrap_or(false) {
            let _ = win.hide();
        } else {
            show_popover(app);
        }
    }
}

fn show_popover(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(POPOVER) {
        // Anchored under the tray icon, centred on it horizontally — an ordinary macOS menu
        // extra. tauri-plugin-positioner tracks the icon's rect via on_tray_event (tray.rs);
        // best-effort, same as the window it replaces: a bad read just leaves the window
        // wherever it last was.
        let _ = win.move_window(Position::TrayBottomCenter);
        let _ = win.show();
        let _ = win.set_focus();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shortcut_whitelist_is_closed() {
        for ok in ["alt+w", "alt+shift+w", "ctrl+alt+w", "cmd+shift+w"] {
            assert!(shortcut_for(ok).is_some(), "{ok}");
        }
        // No ⌥Space (launcher collision) and no arbitrary combos.
        for bad in ["alt+space", "cmd+q", "w", "", "alt+w; rm -rf"] {
            assert!(shortcut_for(bad).is_none(), "{bad:?}");
        }
        assert!(shortcut_for(DEFAULT_SHORTCUT).is_some());
    }
}
