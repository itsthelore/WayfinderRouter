//! Wayfinder Desktop — the Tauri v2 macOS menu-bar shell (WF-ADR-0042).
//!
//! A no-Dock accessory app: the three-state W template tray icon and the ⌥W hotkey both summon
//! one borderless, vibrant popover at the bottom-center of the active display, launcher-style
//! (hide-on-blur, state preserved). The webview talks to the gateway directly over loopback
//! HTTP (not Rust IPC); Rust only drives the window, the tray, and the service-first lifecycle
//! (WF-ROADMAP-0009 Phase 3). The gateway process is owned by the WF-ADR-0038 launchd agent —
//! this app never spawns it, only detects/attaches and offers service control.

mod commands;
mod service;
mod tray;

use tauri::{
    ActivationPolicy, App, AppHandle, Manager, PhysicalPosition, WebviewWindow, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

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
        ])
        .setup(|app| {
            setup(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Hide-on-blur: dismiss the popover to the tray. It is hidden, not destroyed, so
            // the React draft survives the next open (Esc in the webview also just hides it).
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
    // No Dock icon: a menu-bar-only accessory app.
    #[cfg(target_os = "macos")]
    app.set_activation_policy(ActivationPolicy::Accessory);

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
    // Best-effort: a missing Accessibility grant must not stop the tray/popover from working;
    // the hotkey simply stays inactive until the user grants it.
    if let Err(e) = register_toggle_shortcut(app) {
        log::warn!("⌥W global shortcut unavailable (grant Accessibility to enable): {e}");
    }
    Ok(())
}

fn register_toggle_shortcut(app: &App) -> Result<(), Box<dyn std::error::Error>> {
    // ⌥W toggles the popover (maintainer pick — on-brand and unclaimed by macOS; rebinding
    // lands with the Phase 4 settings row).
    let alt_w = Shortcut::new(Some(Modifiers::ALT), Code::KeyW);
    app.global_shortcut()
        .on_shortcut(alt_w, |app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                toggle_popover(app);
            }
        })?;
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
        position_bottom_center(app, &win);
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// Summon launcher-style (amends WF-ADR-0042 §3): bottom-center of the display the cursor is
/// on — falling back to the window's current display, then the primary — lifted clear of the
/// Dock. Best-effort: if no monitor is resolvable the window shows wherever it last was.
fn position_bottom_center(app: &AppHandle, win: &WebviewWindow) {
    let monitor = app
        .cursor_position()
        .ok()
        .and_then(|p| app.monitor_from_point(p.x, p.y).ok().flatten())
        .or_else(|| win.current_monitor().ok().flatten())
        .or_else(|| win.primary_monitor().ok().flatten());
    let (Some(monitor), Ok(size)) = (monitor, win.outer_size()) else {
        return;
    };
    // 96 logical px above the bottom edge clears the default Dock and reads deliberately
    // "floating" when the Dock is hidden.
    let lift = (96.0 * monitor.scale_factor()) as i32;
    let mpos = monitor.position();
    let msize = monitor.size();
    let x = mpos.x + (msize.width as i32 - size.width as i32) / 2;
    let y = mpos.y + msize.height as i32 - size.height as i32 - lift;
    let _ = win.set_position(PhysicalPosition::new(x, y));
}
