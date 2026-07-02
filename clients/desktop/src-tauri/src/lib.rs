//! Wayfinder Desktop — the Tauri v2 macOS menu-bar shell (WF-ADR-0042).
//!
//! A no-Dock accessory app: a template tray icon toggles one borderless, vibrant popover
//! anchored under the tray. The webview talks to the gateway directly over loopback HTTP
//! (not Rust IPC); this shell owns the window/tray/lifecycle. The detect-then-spawn gateway
//! supervisor and the PyInstaller sidecar land in the next steps of Phase 3.

use tauri::{
    image::Image,
    menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    ActivationPolicy, App, AppHandle, Manager, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_positioner::{Position, WindowExt};

const POPOVER: &str = "popover";

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // single-instance must be registered FIRST so a second launch just resurfaces us
        // instead of spinning up a second tray + gateway.
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

    build_tray(app)?;
    // Best-effort: a missing Accessibility grant must not stop the tray/popover from working;
    // the hotkey simply stays inactive until the user grants it.
    if let Err(e) = register_toggle_shortcut(app) {
        log::warn!("⌥Space global shortcut unavailable (grant Accessibility to enable): {e}");
    }
    Ok(())
}

fn build_tray(app: &App) -> tauri::Result<()> {
    // A disabled header line mirrors menubar_core.status_from_health; Start/Stop + service
    // control land with the supervisor (next step). For now: status header + Quit.
    let status = MenuItemBuilder::with_id("status", "Wayfinder")
        .enabled(false)
        .build(app)?;
    let quit = PredefinedMenuItem::quit(app, Some("Quit Wayfinder"))?;
    let menu = MenuBuilder::new(app)
        .item(&status)
        .separator()
        .item(&quit)
        .build()?;

    // Placeholder tray glyph (the app icon). The monochrome waypoint template with three
    // health states (running/degraded/stopped) replaces this once the supervisor drives it.
    let icon = Image::from_bytes(include_bytes!("../icons/32x32.png"))?;

    TrayIconBuilder::with_id("wayfinder")
        .icon(icon)
        .icon_as_template(false)
        .menu(&menu)
        .show_menu_on_left_click(false) // left-click toggles the popover; right-click opens the menu
        .on_tray_icon_event(|tray, event| {
            // Let the positioner record the tray rect so TrayCenter anchoring works.
            tauri_plugin_positioner::on_tray_event(tray.app_handle(), &event);
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                toggle_popover(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn register_toggle_shortcut(app: &App) -> Result<(), Box<dyn std::error::Error>> {
    // ⌥Space toggles the popover. Rebinding + collision handling come in a later step.
    let alt_space = Shortcut::new(Some(Modifiers::ALT), Code::Space);
    app.global_shortcut()
        .on_shortcut(alt_space, |app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                toggle_popover(app);
            }
        })?;
    Ok(())
}

fn toggle_popover(app: &AppHandle) {
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
        // Anchor centered under the menu-bar tray icon, then reveal + focus.
        let _ = win.move_window(Position::TrayCenter);
        let _ = win.show();
        let _ = win.set_focus();
    }
}
