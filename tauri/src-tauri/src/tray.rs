//! Tray icon setup. Returns the TrayIcon so `lib.rs::setup` can store it in
//! AppState for later badge updates (`commands::tray::set_tray_badge`).
use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{TrayIcon, TrayIconBuilder},
    App,
};

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
    let quit_item = MenuItem::with_id(app, "quit", "Quit NarraNexus", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&start_item, &stop_item, &quit_item])?;

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
            "quit" => {
                log::info!("Tray: Quit requested");
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;

    Ok(tray)
}
