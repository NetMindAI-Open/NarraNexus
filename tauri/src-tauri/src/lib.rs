mod commands;
mod sidecar;
mod state;
mod tray;

use state::AppState;

pub fn run() {
    env_logger::init();

    let app_state = AppState::default();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(app_state)
        .invoke_handler(tauri::generate_handler![
            commands::service::get_service_status,
            commands::service::start_all_services,
            commands::service::stop_all_services,
            commands::service::restart_service,
            commands::config::get_app_config,
            commands::config::get_app_mode,
            commands::config::set_app_mode,
            commands::health::get_health_status,
            commands::health::get_logs,
        ])
        .setup(|app| {
            tray::create_tray(app)?;
            log::info!("NarraNexus started");
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                log::info!("Window close requested, stopping services...");
                let state = window.state::<AppState>();
                let rt = tokio::runtime::Runtime::new().unwrap();
                rt.block_on(async {
                    state.process_manager.lock().await.stop_all().await;
                });
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running NarraNexus");
}
