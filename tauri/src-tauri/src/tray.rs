//! Tray icon setup. Returns the TrayIcon so `lib.rs::setup` can store it in
//! AppState for later badge updates (`commands::tray::set_tray_badge`).
//!
//! The "Check for Updates…" item also doubles as the updater state surface:
//! a Rust-side `updater:state` event listener (installed below) rewrites
//! the item's label to reflect the current pipeline phase. So one menu item
//! handles three different verbs — `Check`, `Downloading 35%`, `Restart` —
//! and the click handler dispatches based on the current state.
use tauri::{
    image::Image,
    menu::{MenuBuilder, MenuItem},
    tray::{TrayIcon, TrayIconBuilder},
    App, Listener, Manager,
};

use crate::commands::updater::{run_pipeline, UpdaterState};
use crate::state::AppState;

// The menu-bar icon, embedded at compile time (no runtime path resolution).
// It's the NarraNexus v2 mark rendered as a monochrome black+alpha template;
// `.icon_as_template(true)` lets macOS tint it for the light/dark menu bar so
// it stays visible either way. Without an icon the tray rendered blank.
const TRAY_ICON_PNG: &[u8] = include_bytes!("../icons/tray@2x.png");

/// Translate the updater state machine into a tray-menu item label.
/// Kept tiny + side-effect-free so the live `set_text` from the event
/// listener stays trivial.
fn updater_label(state: &UpdaterState) -> String {
    match state {
        UpdaterState::Idle => "Check for Updates…".to_string(),
        UpdaterState::Checking => "Checking for updates…".to_string(),
        UpdaterState::UpToDate { current, .. } => {
            format!("Up to date ({current})")
        }
        UpdaterState::Available { version, .. } => {
            format!("Update {version} found — downloading…")
        }
        UpdaterState::Downloading {
            percent: Some(p), ..
        } => format!("Downloading update ({p}%)"),
        UpdaterState::Downloading { percent: None, .. } => "Downloading update…".to_string(),
        UpdaterState::Installing { version } => format!("Installing {version}…"),
        UpdaterState::Ready { version } => format!("Restart to apply {version}"),
        UpdaterState::Failed { stage, .. } => format!("Check for Updates… (last {stage} failed)"),
    }
}

pub fn create_tray(app: &App) -> Result<TrayIcon, Box<dyn std::error::Error>> {
    let start_item =
        MenuItem::with_id(app, "start_all", "Start All Services", true, None::<&str>)?;
    let stop_item =
        MenuItem::with_id(app, "stop_all", "Stop All Services", true, None::<&str>)?;
    // "Check for Updates…" — also the live status surface for the updater
    // state machine. The label is rewritten by the `updater:state` event
    // listener installed below; clicking dispatches based on the current
    // state (Idle → kick pipeline; Ready → restart). See updater.rs for
    // the full state machine.
    let check_updates_item = MenuItem::with_id(
        app,
        "updater_action",
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

    // Hold a cheap clone for the event listener (MenuItem is internally
    // Arc'd in Tauri 2, so .clone() does not duplicate the OS-level
    // widget; it just bumps the refcount).
    let label_handle = check_updates_item.clone();
    let app_handle = app.handle().clone();
    // Subscribe to the pipeline's state events and rewrite the label
    // live. Listener is detached intentionally — its lifetime is the
    // app's lifetime; the closure holds the menu-item handle by move,
    // which outlives every plausible state transition.
    app_handle.listen("updater:state", move |event| {
        let payload = event.payload();
        match serde_json::from_str::<UpdaterState>(payload) {
            Ok(state) => {
                let text = updater_label(&state);
                if let Err(e) = label_handle.set_text(&text) {
                    log::warn!("[tray] failed to update updater label: {e}");
                }
            }
            Err(e) => log::warn!("[tray] could not parse updater:state payload: {e}"),
        }
    });

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
            "updater_action" => {
                // Dispatch by the CURRENT updater state. The label tells
                // the user what the click will do; this matches that.
                let snapshot = {
                    let state = app.state::<AppState>();
                    state
                        .updater_state
                        .lock()
                        .ok()
                        .map(|g| g.clone())
                        .unwrap_or(UpdaterState::Idle)
                };
                match snapshot {
                    UpdaterState::Ready { .. } => {
                        log::info!("Tray: user clicked Restart on Ready state");
                        app.restart();
                    }
                    UpdaterState::Downloading { .. }
                    | UpdaterState::Installing { .. }
                    | UpdaterState::Checking
                    | UpdaterState::Available { .. } => {
                        // Pipeline already running — clicking again is
                        // intentionally a no-op so the user can't
                        // accidentally pile concurrent checks.
                        log::info!(
                            "Tray: updater click while pipeline in flight — ignored"
                        );
                    }
                    _ => {
                        log::info!("Tray: user requested update check");
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            run_pipeline(app).await;
                        });
                    }
                }
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
