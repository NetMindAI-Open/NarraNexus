//! @file_name: updater.rs
//! @description: Unified auto-updater state machine.
//!
//! Three entry points (startup auto-check, tray "Check for Updates…",
//! Settings page button) all feed the same pipeline. A single
//! `UpdaterState` field on `AppState` is the source of truth; every
//! transition emits a `updater:state` Tauri event, and every UI surface
//! (global banner, Settings panel, tray menu label) just mirrors the
//! event payload. There is no per-surface logic anywhere — that is the
//! whole point of this rewrite (Owner spec, 2026-05-27).
//!
//! State machine
//! -------------
//!     Idle ──check──► Checking ──┬─► UpToDate { v, at }
//!                                └─► Available { v, notes }
//!                                       │ (auto, no user gate)
//!                                       ▼
//!                                  Downloading { downloaded, total, % }
//!                                       │ (events throttled to ~250 ms)
//!                                       ▼
//!                                   Installing { version }
//!                                       │
//!                                       ▼
//!                                   Ready { version }   ── user clicks
//!                                       │                  Restart
//!                                       └─────► app.restart()
//!
//!     Any stage → Failed { stage, error }
//!
//! Auto-install behavior (Q1=A in the design): once `Available` is
//! reached, the pipeline downloads + installs WITHOUT asking the user.
//! The "ask" is the Ready banner at the end. This makes updates feel
//! instant — by the time the user sees the banner the bytes are already
//! on disk.
//!
//! Requires (see `tauri.conf.json` `plugins.updater`): a non-empty
//! `pubkey` and the release signed with `TAURI_SIGNING_PRIVATE_KEY`,
//! plus `latest.json` + `.app.tar.gz` published at the `endpoints`
//! URL. Without those the check errors and the state machine
//! transitions to Failed — the app keeps working.

use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::UpdaterExt;
use tokio::sync::Mutex as TokioMutex;

use crate::state::AppState;

/// Serializable state machine the frontend mirrors via the
/// `updater:state` Tauri event.
///
/// `#[serde(tag = "kind", rename_all = "snake_case")]` produces JSON
/// like `{"kind": "downloading", "downloaded": 12345, ...}` — discriminated
/// unions the frontend Zustand store unpacks into a TypeScript union type.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum UpdaterState {
    Idle,
    Checking,
    UpToDate {
        current: String,
        /// Unix epoch seconds — frontend formats as "checked 2 min ago".
        checked_at: u64,
    },
    Available {
        version: String,
        notes: Option<String>,
    },
    Downloading {
        downloaded: u64,
        total: Option<u64>,
        /// 0..=100. None when total is unknown (server omitted Content-Length).
        percent: Option<u8>,
    },
    Installing {
        version: String,
    },
    Ready {
        version: String,
    },
    Failed {
        stage: &'static str, // "check" | "download" | "install"
        error: String,
    },
}

/// Reentrancy guard. Prevents two pipelines from racing (e.g. tray click
/// arriving while startup auto-check is mid-download) — a second call
/// while a pipeline is in flight is a no-op that just returns the
/// current state. tokio::sync::Mutex because we hold it across awaits.
static PIPELINE_LOCK: tokio::sync::OnceCell<Arc<TokioMutex<()>>> =
    tokio::sync::OnceCell::const_new();

async fn pipeline_lock() -> &'static Arc<TokioMutex<()>> {
    PIPELINE_LOCK
        .get_or_init(|| async { Arc::new(TokioMutex::new(())) })
        .await
}

fn now_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Mutate the shared state AND emit the `updater:state` event in one
/// step. Every transition in this module goes through here so the
/// UI surfaces never see a stale store.
fn set_state(app: &AppHandle, next: UpdaterState) {
    let state = app.state::<AppState>();
    if let Ok(mut guard) = state.updater_state.lock() {
        *guard = next.clone();
    }
    // Errors emitting are non-fatal — UI just won't update live, but
    // the next `updater_get_state` poll will see the new value.
    if let Err(e) = app.emit("updater:state", &next) {
        log::warn!("[updater] failed to emit updater:state event: {e}");
    }
}

/// Run the full check → download → install pipeline. Public so all three
/// entry points (startup hook, tray, Settings command) share it.
///
/// Returns immediately if another pipeline is already in flight — that
/// pipeline's events keep flowing to the frontend, so calling this
/// while one is running is a no-op for state.
pub async fn run_pipeline(app: AppHandle) {
    let lock = pipeline_lock().await;
    // try_lock so a second concurrent call just bails; we do NOT want
    // it queued behind the first (would cause a second check + download
    // after the first finishes, which is wasteful and confusing).
    let _guard = match lock.try_lock() {
        Ok(g) => g,
        Err(_) => {
            log::info!("[updater] pipeline already in flight — skipping new request");
            return;
        }
    };

    set_state(&app, UpdaterState::Checking);

    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            set_state(
                &app,
                UpdaterState::Failed {
                    stage: "check",
                    error: format!("updater not available: {e}"),
                },
            );
            return;
        }
    };

    // Bound the check to 30 s — `updater.check()` is a single HTTP GET
    // against the configured endpoint, but reqwest's default has no
    // hard connect timeout, so an unreachable endpoint would hang the
    // pipeline indefinitely and the UI would spin forever (the
    // 2026-05-27 v1.7.5 incident).
    let check_result = tokio::time::timeout(Duration::from_secs(30), updater.check()).await;

    let update_opt = match check_result {
        Err(_) => {
            set_state(
                &app,
                UpdaterState::Failed {
                    stage: "check",
                    error:
                        "update check timed out after 30s (network unreachable or no VPN?)"
                            .to_string(),
                },
            );
            return;
        }
        Ok(Err(e)) => {
            set_state(
                &app,
                UpdaterState::Failed {
                    stage: "check",
                    error: format!("update check failed: {e}"),
                },
            );
            return;
        }
        Ok(Ok(opt)) => opt,
    };

    let update = match update_opt {
        Some(u) => u,
        None => {
            // Already on the latest version. Read the current version
            // from the running binary so the frontend can render
            // "You're on 1.7.10 (latest)".
            let current = app.package_info().version.to_string();
            set_state(
                &app,
                UpdaterState::UpToDate {
                    current,
                    checked_at: now_epoch(),
                },
            );
            return;
        }
    };

    // Available → emit the transient "Available" state so any banner /
    // panel can briefly show "Update X found, downloading…" before the
    // first progress event arrives.
    let version = update.version.clone();
    let notes = update.body.clone();
    set_state(
        &app,
        UpdaterState::Available {
            version: version.clone(),
            notes,
        },
    );

    // Progress throttle (Q3=250ms). Without this we would emit one
    // Tauri event per chunk — at ~16 KB per chunk on a 400 MB bundle
    // that's ~25k events, enough to wedge the WS / IPC channel.
    let downloaded = Arc::new(std::sync::Mutex::new(0u64));
    let total = Arc::new(std::sync::Mutex::new(None::<u64>));
    let last_emit = Arc::new(std::sync::Mutex::new(
        Instant::now() - Duration::from_secs(1),
    ));

    let app_for_chunk = app.clone();
    let downloaded_for_chunk = downloaded.clone();
    let total_for_chunk = total.clone();
    let last_emit_for_chunk = last_emit.clone();

    let app_for_done = app.clone();
    let version_for_done = version.clone();

    let install_result = update
        .download_and_install(
            move |chunk_size: usize, content_length: Option<u64>| {
                if let Ok(mut d) = downloaded_for_chunk.lock() {
                    *d += chunk_size as u64;
                }
                if let Some(cl) = content_length {
                    if let Ok(mut t) = total_for_chunk.lock() {
                        *t = Some(cl);
                    }
                }
                let mut last = match last_emit_for_chunk.lock() {
                    Ok(g) => g,
                    Err(_) => return,
                };
                if last.elapsed() < Duration::from_millis(250) {
                    return;
                }
                *last = Instant::now();
                drop(last);

                let d = *downloaded_for_chunk.lock().unwrap();
                let t = *total_for_chunk.lock().unwrap();
                let pct = t.map(|tt| {
                    if tt == 0 {
                        0
                    } else {
                        ((d as f64 / tt as f64) * 100.0).min(100.0) as u8
                    }
                });
                set_state(
                    &app_for_chunk,
                    UpdaterState::Downloading {
                        downloaded: d,
                        total: t,
                        percent: pct,
                    },
                );
            },
            move || {
                // Download finished, install (extract + .app swap) is
                // starting. tauri-plugin-updater calls this callback
                // synchronously between download and install on macOS.
                set_state(
                    &app_for_done,
                    UpdaterState::Installing {
                        version: version_for_done.clone(),
                    },
                );
            },
        )
        .await;

    match install_result {
        Ok(()) => {
            log::info!("[updater] installed update {version} (applies on restart)");
            set_state(&app, UpdaterState::Ready { version });
        }
        Err(e) => {
            // Could be download (network) or install (write-perm / swap)
            // failure — tauri-plugin-updater doesn't tell us which. We
            // tag stage="download" because that's the more common
            // failure point; the error message itself will say
            // "permission denied" / "EXDEV" / etc. for the install case.
            set_state(
                &app,
                UpdaterState::Failed {
                    stage: "download",
                    error: format!("download/install failed: {e}"),
                },
            );
        }
    }
}

// ── IPC commands ────────────────────────────────────────────────────────

/// Kick a fresh check → download → install pipeline.
/// All three UI entry points (tray, Settings, startup) call this
/// (startup just calls `run_pipeline` directly, no IPC needed).
/// Returns immediately; progress arrives via `updater:state` events.
#[tauri::command]
pub async fn updater_check(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn(async move { run_pipeline(app).await });
    Ok(())
}

/// Snapshot the current state. Called by the frontend on mount to
/// initialise its store before any event arrives (otherwise the store
/// would sit at `Idle` even if a startup-auto pipeline already
/// transitioned past it).
#[tauri::command]
pub fn updater_get_state(app: AppHandle) -> UpdaterState {
    let state = app.state::<AppState>();
    state
        .updater_state
        .lock()
        .ok()
        .map(|g| g.clone())
        .unwrap_or(UpdaterState::Idle)
}

/// Restart the app to apply a downloaded update. Frontend gates the
/// button on `state.kind === "ready"`. We don't validate here because
/// `app.restart()` is harmless even when no update is pending — it
/// just relaunches the current binary.
#[tauri::command]
pub fn updater_restart(app: AppHandle) {
    log::info!("[updater] user accepted restart");
    app.restart();
}

// ── Startup hook ────────────────────────────────────────────────────────

/// Called once from `lib.rs::setup` (bundled mode only). Just kicks the
/// pipeline — events flow to the (not-yet-mounted) frontend, which will
/// pick up the state via `updater_get_state` when it mounts and via
/// live `updater:state` events thereafter.
pub async fn run_startup_pipeline(app: AppHandle) {
    run_pipeline(app).await;
}
