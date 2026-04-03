use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    App,
};

pub fn create_tray(app: &App) -> Result<(), Box<dyn std::error::Error>> {
    let start_item =
        MenuItem::with_id(app, "start_all", "Start All Services", true, None::<&str>)?;
    let stop_item =
        MenuItem::with_id(app, "stop_all", "Stop All Services", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit NarraNexus", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&start_item, &stop_item, &quit_item])?;

    TrayIconBuilder::new()
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

    Ok(())
}
