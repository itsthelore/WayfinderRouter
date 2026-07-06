//! The menu-bar tray (WF-ROADMAP-0009 Phase 3): the three-state W template icon plus the native
//! right-click service menu. Left-click toggles the popover; the menu drives the service-first
//! lifecycle (Start/Stop/Install) and opens fixed local targets. The icon + title are updated
//! from the webview's healthz poll via the `set_tray_state` command — this module only builds
//! the tray and routes its menu events.

use tauri::menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::App;

use crate::commands::{open_internal, tray_image};
use crate::service::{self, ServiceAction};

pub fn build(app: &App) -> tauri::Result<()> {
    let status = MenuItemBuilder::with_id("status", "Wayfinder").enabled(false).build(app)?;
    let start = MenuItemBuilder::with_id("svc:start", "Start gateway").build(app)?;
    let stop = MenuItemBuilder::with_id("svc:stop", "Stop gateway").build(app)?;
    let install = MenuItemBuilder::with_id("svc:install", "Install service…").build(app)?;
    let dashboard = MenuItemBuilder::with_id("open:dashboard", "Open dashboard").build(app)?;
    let config = MenuItemBuilder::with_id("open:config", "Open config").build(app)?;
    let logs = MenuItemBuilder::with_id("open:logs", "Open logs").build(app)?;
    let quit = PredefinedMenuItem::quit(app, Some("Quit Wayfinder"))?;
    let menu = MenuBuilder::new(app)
        .item(&status)
        .separator()
        .item(&start)
        .item(&stop)
        .item(&install)
        .separator()
        .item(&dashboard)
        .item(&config)
        .item(&logs)
        .separator()
        .item(&quit)
        .build()?;

    // Start on the stopped W; the webview's first healthz poll swaps it (running/degraded) and
    // sets the savings title. Template so macOS tints it for the menu-bar appearance.
    TrayIconBuilder::with_id("wayfinder")
        .icon(tray_image("stopped"))
        .icon_as_template(true)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|_app, event| handle_menu(event.id.as_ref()))
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                crate::toggle_popover(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn handle_menu(id: &str) {
    // Service actions can block for a second (launchctl bootstrap); run off the UI thread. The
    // webview's healthz poll reflects the new state on its next tick — no manual tray refresh.
    if let Some(action) = match id {
        "svc:install" => Some(ServiceAction::Install),
        "svc:start" => Some(ServiceAction::Start),
        "svc:stop" => Some(ServiceAction::Stop),
        _ => None,
    } {
        std::thread::spawn(move || match service::run(action) {
            Ok(msg) => log::info!("tray service action: {msg}"),
            Err(e) => log::error!("tray service action: {e}"),
        });
        return;
    }
    let target = match id {
        "open:dashboard" => "dashboard",
        "open:config" => "config",
        "open:logs" => "logs",
        _ => return,
    };
    if let Err(e) = open_internal(target) {
        log::error!("tray open {target}: {e}");
    }
}
