//! Tray icon setup. Returns the TrayIcon so `lib.rs::setup` can store it in
//! AppState for later badge updates (`commands::tray::set_tray_badge`).
use tauri::{
    image::Image,
    menu::{MenuBuilder, MenuItem},
    tray::{TrayIcon, TrayIconBuilder},
    App,
};

use crate::commands::updater::run_manual_update_check;

// The menu-bar icon, embedded at compile time (no runtime path resolution).
// It's the NarraNexus v2 mark rendered as a monochrome black+alpha template;
// `.icon_as_template(true)` lets macOS tint it for the light/dark menu bar so
// it stays visible either way. Without an icon the tray rendered blank.
const TRAY_ICON_PNG: &[u8] = include_bytes!("../icons/tray@2x.png");

pub fn create_tray(app: &App) -> Result<TrayIcon, Box<dyn std::error::Error>> {
    let start_item =
        MenuItem::with_id(app, "start_all", "Start All Services", true, None::<&str>)?;
    let stop_item =
        MenuItem::with_id(app, "stop_all", "Stop All Services", true, None::<&str>)?;
    // "Check for Updates…" — manual trigger for the auto-update flow.
    // Auto-update on startup is already wired (lib.rs setup), but a
    // manual entry point matters because (a) users want to know they're
    // current without restarting, and (b) the startup check runs early
    // and silently — if it fails (network blip, signing-key mismatch)
    // they have no other path to retry. See updater.rs for the
    // dialog-always-shown UX.
    let check_updates_item = MenuItem::with_id(
        app,
        "check_updates",
        "Check for Updates…",
        true,
        None::<&str>,
    )?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit NarraNexus", true, None::<&str>)?;

    // MenuBuilder handles heterogeneous items (MenuItem + separators)
    // without the `&[&dyn IsMenuItem<R>]` cast dance that Menu::with_items
    // requires for a mixed slice.
    let menu = MenuBuilder::new(app)
        .item(&start_item)
        .item(&stop_item)
        .separator()
        .item(&check_updates_item)
        .separator()
        .item(&quit_item)
        .build()?;

    let icon = Image::from_bytes(TRAY_ICON_PNG)?;

    let tray = TrayIconBuilder::new()
        .icon(icon)
        .icon_as_template(true)
        .menu(&menu)
        .tooltip("NarraNexus")
        .on_menu_event(|app, event| match event.id.as_ref() {
            "start_all" => {
                log::info!("Tray: Start all services requested");
            }
            "stop_all" => {
                log::info!("Tray: Stop all services requested");
            }
            "check_updates" => {
                log::info!("Tray: Check for updates requested");
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    run_manual_update_check(app).await;
                });
            }
            "quit" => {
                log::info!("Tray: Quit requested");
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;

    Ok(tray)
}
