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
        // Single-instance plugin MUST be registered before anything that
        // does work in setup() — its callback fires in the live (first)
        // process whenever a second `narranexus` is launched, then the
        // second process exits non-zero before any sidecar spawn. Without
        // this, double-clicking the .app twice fast (or relaunching after
        // a crash that left orphans) tries to start a second full sidecar
        // stack on the same hardcoded ports → user sees "address already
        // in use" with no path to recover.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            log::info!("Second NarraNexus instance attempted — focusing existing window");
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.unminimize();
                let _ = win.show();
                let _ = win.set_focus();
            }
        }))
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
            commands::auth::trigger_claude_login,
            commands::auth::trigger_claude_logout,
            commands::auth::cancel_claude_login,
            commands::auth::get_claude_login_status,
        ])
        .setup(|app| {
            // Port-conflict preflight. Must run before anything else: if a
            // required port (8000 / 8100 / 7801 / 7830) is held by another
            // process, spawning the Python sidecars will silently fail
            // (bind error → child exits → black screen forever with no
            // visible log). The preflight shows a native dialog explaining
            // which port is stuck on which process and exits cleanly.
            // See sidecar/port_preflight.rs for the 3-step plan this is
            // the first iteration of.
            let port_conflicts = sidecar::port_preflight::check_required_ports();
            if !port_conflicts.is_empty() {
                sidecar::port_preflight::show_conflict_dialog_and_exit(&port_conflicts);
            }

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
            //
            // Intentionally verbose drop order: newer rustc (1.80+) tightened
            // temporary-scope rules so that `if let Ok(..) = state.tray_handle.lock()`
            // holds the MutexGuard temporary until the end of the enclosing block,
            // which outlives the inner `state` binding and produces
            // "does not live long enough" (E0597). Binding the lock result
            // explicitly makes the drop sequence trivially correct regardless of
            // rustc version.
            let tray = tray::create_tray(app)?;
            {
                let state = app.state::<AppState>();
                let lock_result = state.tray_handle.lock();
                if let Ok(mut guard) = lock_result {
                    *guard = Some(tray);
                }
            }

            // Kick off the lark-cli + lark skill-pack preflight in parallel
            // with service startup. It is entirely optional — Lark features
            // degrade gracefully if `npm`/`node` are missing or the install
            // fails/times out. Mirrors scripts/run.sh `check_deps`.
            sidecar::lark_preflight::run_preflight();

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
                // Window close → defer to the unified ExitRequested
                // handler below. We just log here so debugging stays easy;
                // services are stopped exactly once in app.run() to avoid
                // racing with concurrent CloseRequested + Cmd+Q paths.
                log::info!("Window close requested for {:?}", window.label());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building NarraNexus")
        .run(|app_handle, event| {
            // Single chokepoint for tearing down sidecars. Every exit
            // path Tauri knows about (Cmd+Q, tray Quit / app.exit(0),
            // Dock quit, system logout/shutdown, last window close on
            // platforms where that exits the app) ultimately fires
            // ExitRequested before the runtime tears down. Doing the
            // cleanup here, instead of in tray + on_window_event +
            // wherever, eliminates the bypass that left orphan Python
            // sidecars holding ports 8000 / 8100 / 7801 / 7830 across
            // launches.
            if let tauri::RunEvent::ExitRequested { code, .. } = event {
                log::info!("ExitRequested (code={:?}) — stopping services", code);
                let state = app_handle.state::<AppState>();
                let pm = state.process_manager.clone();
                tauri::async_runtime::block_on(async move {
                    pm.lock().await.stop_all().await;
                });
                log::info!("All services stopped, runtime will now exit");
            }
        });
}
