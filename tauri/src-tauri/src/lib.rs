mod commands;
mod sidecar;
mod state;
mod tray;

use tauri::Manager;

use state::{resolve_db_path, resolve_project_root, AppState};

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
            commands::tray::set_tray_badge,
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

            // Point every child process at the SQLite proxy so they go through
            // one arbiter instead of fighting over the raw DB file. Mirrors
            // `scripts/dev-local.sh`'s ENV_CMD. Without this the agent loop
            // hangs the first time chat triggers multi-process DB writes.
            // Keep in sync with SQLite Proxy port in state.rs bundled_services.
            std::env::set_var("SQLITE_PROXY_URL", "http://localhost:8100");
            std::env::set_var("SQLITE_PROXY_PORT", "8100");

            // Dashboard v2 (TDR-7): keep the TrayIcon handle in AppState so that
            // `commands::tray::set_tray_badge` can update its title later.
            let tray = tray::create_tray(app)?;
            {
                let state = app.state::<AppState>();
                if let Ok(mut guard) = state.tray_handle.lock() {
                    *guard = Some(tray);
                }
            }

            // Auto-start Python services in local mode (non-blocking)
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let state = app_handle.state::<AppState>();
                let defs = state.service_defs.clone();
                let project_root = resolve_project_root();
                let project_root_str = project_root.to_string_lossy().to_string();
                let mut pm = state.process_manager.lock().await;

                if let Err(e) = pm.start_all(&defs, &project_root_str).await {
                    log::error!("Failed to auto-start services: {}", e);
                } else {
                    log::info!("All services started successfully");
                }
            });

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
