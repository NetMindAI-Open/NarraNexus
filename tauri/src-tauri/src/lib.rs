mod commands;
mod sidecar;
mod state;
mod tray;

use tauri::Manager;

use state::{resolve_db_path, AppState};

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
            // Set DATABASE_URL so the Python backend picks up the correct SQLite path
            let db_path = resolve_db_path();
            if let Some(parent) = db_path.parent() {
                std::fs::create_dir_all(parent).ok();
            }
            std::env::set_var(
                "DATABASE_URL",
                format!("sqlite:///{}", db_path.display()),
            );

            tray::create_tray(app)?;
            log::info!("NarraNexus started, DB: {}", db_path.display());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                log::info!("Window close requested, stopping services...");
                let state: tauri::State<'_, AppState> = window.state();
                let pm = state.process_manager.clone();
                let rt = tokio::runtime::Runtime::new().unwrap();
                rt.block_on(async {
                    pm.lock().await.stop_all().await;
                });
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running NarraNexus");
}
