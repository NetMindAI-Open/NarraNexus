use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex as StdMutex};
use tauri::tray::TrayIcon;
use tokio::sync::Mutex;

use crate::sidecar::health_monitor::HealthMonitor;
use crate::sidecar::process_manager::ProcessManager;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum AppMode {
    Local,
    CloudApp,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum UserType {
    Internal,
    External,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub mode: AppMode,
    pub user_type: UserType,
    pub api_base_url: String,
    pub python_path: Option<String>,
    pub db_path: Option<String>,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            mode: AppMode::Local,
            user_type: UserType::Internal,
            api_base_url: "http://localhost:8000".to_string(),
            python_path: None,
            db_path: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceDef {
    pub id: String,
    pub label: String,
    pub command: String,
    pub args: Vec<String>,
    pub cwd: Option<String>,
    pub port: Option<u16>,
    pub health_url: Option<String>,
    pub order: u32,
    /// Optional delay (ms) to wait AFTER spawning this service before starting
    /// the next one. Used to mirror `scripts/dev-local.sh`'s explicit
    /// `sleep 3` after `sqlite_proxy_server`, which every downstream service
    /// depends on.
    #[serde(default)]
    pub startup_delay_ms: Option<u64>,
}

// ---------------------------------------------------------------------------
// Path resolution helpers
// ---------------------------------------------------------------------------

/// Resolve the Resources directory.
/// In a macOS .app bundle the executable lives at Contents/MacOS/narranexus,
/// so Contents/Resources is one level up from the executable directory.
/// Falls back to the current working directory in development.
pub fn resolve_resource_dir() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        let exe_dir = exe.parent().unwrap_or(Path::new("."));
        // .app bundle: exe is at Contents/MacOS/narranexus
        let resources = exe_dir.join("../Resources");
        if resources.exists() {
            return resources.canonicalize().unwrap_or(resources);
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// Resolve the project root that contains backend/, src/, etc.
/// Bundle mode: Contents/Resources/resources/project/  (Tauri nests under resources/)
/// Dev mode: two levels up from src-tauri (i.e. the repo root).
pub fn resolve_project_root() -> PathBuf {
    let resources = resolve_resource_dir();
    // Tauri bundles src-tauri/resources/ → Contents/Resources/resources/
    for subdir in &["resources/project", "project"] {
        let project = resources.join(subdir);
        if project.exists() {
            return project;
        }
    }
    // Development: CWD is typically tauri/src-tauri or tauri/
    std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("..")
}

/// Resolve the Python interpreter path.
/// Bundle mode: Contents/Resources/resources/python/bin/python3
/// Dev mode: fall back to "uv" (used via `uv run`).
pub fn resolve_python_path() -> PathBuf {
    let resources = resolve_resource_dir();
    // Tauri nests under resources/
    for subdir in &["resources/python/bin/python3", "python/bin/python3"] {
        let python = resources.join(subdir);
        if python.exists() {
            return python;
        }
    }
    PathBuf::from("uv")
}

/// Returns true when we are running inside a .app bundle
pub fn is_bundled() -> bool {
    let resources = resolve_resource_dir();
    resources.join("resources/python/bin/python3").exists()
        || resources.join("python/bin/python3").exists()
}

/// Directories containing bundled Node.js + CLI shims that Python child
/// processes need on PATH.
///
/// Why PATH-prepending matters:
///   The Python `claude_agent_sdk` package spawns the `claude` CLI as a
///   subprocess — it looks for `claude` on PATH. A Mac .app launched from
///   Finder inherits the launchd minimal PATH
///   (`/usr/bin:/bin:/usr/sbin:/sbin`), which never contains `claude`. Without
///   this prefix, every chat turn ends with "claude: command not found".
///
/// Return order matters: entries earlier in the Vec are earlier on PATH. We
/// put `bin/` (real `node`) BEFORE `node_modules/.bin/` (shim scripts) so
/// that when `claude`'s shebang does `#!/usr/bin/env node`, `env` finds our
/// bundled node, not some other node the user happens to have.
pub fn resolve_bundled_node_bins() -> Vec<PathBuf> {
    let resources = resolve_resource_dir();
    let mut out = Vec::new();
    for subdir in &["resources/nodejs", "nodejs"] {
        let root = resources.join(subdir);
        if !root.exists() {
            continue;
        }
        out.push(root.join("bin"));
        out.push(root.join("node_modules/.bin"));
        break;
    }
    out
}

/// Resolve the SQLite database path using platform app-data directory.
pub fn resolve_db_path() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".narranexus")
        .join("nexus.db")
}

// is_bundled() defined above with resolve_python_path()

// ---------------------------------------------------------------------------
// ServiceDef factories
// ---------------------------------------------------------------------------

impl ServiceDef {
    pub fn default_services(project_root: &str, python_path: &str, bundled: bool) -> Vec<ServiceDef> {
        if bundled {
            Self::bundled_services(project_root, python_path)
        } else {
            Self::dev_services(project_root)
        }
    }

    /// Services when running from a packaged .app bundle.
    /// Uses the standalone Python interpreter directly (no uv).
    ///
    /// MUST stay in lockstep with `scripts/dev-local.sh` (CLAUDE.md iron rule #7):
    /// the dev script and the bundled app run the exact same set of services
    /// in the exact same order.
    fn bundled_services(project_root: &str, python_path: &str) -> Vec<ServiceDef> {
        vec![
            // Order 0: SQLite Proxy — MUST start first. All other services
            // depend on it for cross-process DB serialization (without it
            // backend/mcp/poller fight over the same SQLite file lock and
            // chats hang in the agent loop).
            ServiceDef {
                id: "sqlite_proxy".to_string(),
                label: "SQLite Proxy".to_string(),
                command: python_path.to_string(),
                args: vec![
                    "-m".to_string(),
                    "xyz_agent_context.utils.sqlite_proxy_server".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: Some(8100),
                health_url: None,
                order: 0,
                // Give uvicorn a moment to bind :8100 before the dependents
                // start. Mirrors `sleep 3` in scripts/dev-local.sh.
                startup_delay_ms: Some(3000),
            },
            ServiceDef {
                id: "backend".to_string(),
                label: "Backend API".to_string(),
                command: python_path.to_string(),
                // Dashboard v2 TDR-12: local mode MUST bind loopback.
                // lifespan in backend/main.py also asserts via DASHBOARD_BIND_HOST env
                // (set in process_manager.rs for id=="backend").
                //
                // --ws-ping-interval / --ws-ping-timeout mirror scripts/dev-local.sh:
                // uvicorn defaults (20s/20s) prematurely drop the chat SSE/WS stream
                // while an Agent loop is waiting on an LLM call. 30s/60s keeps the
                // connection alive across slower model turns. Iron rule #7 alignment.
                args: vec![
                    "-m".to_string(),
                    "uvicorn".to_string(),
                    "backend.main:app".to_string(),
                    "--host".to_string(),
                    "127.0.0.1".to_string(),
                    "--port".to_string(),
                    "8000".to_string(),
                    "--ws-ping-interval".to_string(),
                    "30".to_string(),
                    "--ws-ping-timeout".to_string(),
                    "60".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: Some(8000),
                health_url: Some("/docs".to_string()),
                order: 1,
                startup_delay_ms: None,
            },
            ServiceDef {
                id: "mcp".to_string(),
                label: "MCP Server".to_string(),
                command: python_path.to_string(),
                args: vec![
                    "src/xyz_agent_context/module/module_runner.py".to_string(),
                    "mcp".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 2,
                startup_delay_ms: None,
            },
            ServiceDef {
                id: "poller".to_string(),
                label: "Module Poller".to_string(),
                command: python_path.to_string(),
                args: vec![
                    "-m".to_string(),
                    "xyz_agent_context.services.module_poller".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 3,
                startup_delay_ms: None,
            },
            ServiceDef {
                id: "job_trigger".to_string(),
                label: "Job Trigger".to_string(),
                command: python_path.to_string(),
                args: vec![
                    "src/xyz_agent_context/module/job_module/job_trigger.py".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 4,
                startup_delay_ms: None,
            },
            ServiceDef {
                id: "message_bus_trigger".to_string(),
                label: "Bus Trigger".to_string(),
                command: python_path.to_string(),
                args: vec![
                    "-m".to_string(),
                    "xyz_agent_context.message_bus.message_bus_trigger".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 5,
                startup_delay_ms: None,
            },
            // Consolidated IM channel triggers — one supervisor process runs
            // every channel (Lark / Slack / Telegram / Discord / WeChat /
            // NarraMessenger) in a single event loop, replacing the old
            // one-ServiceDef-per-channel layout.
            ServiceDef {
                id: "channel_triggers".to_string(),
                label: "Channel Triggers".to_string(),
                command: python_path.to_string(),
                args: vec![
                    "-m".to_string(),
                    "xyz_agent_context.module.run_channel_triggers".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 6,
                startup_delay_ms: None,
            },
        ]
    }

    /// Services during development — uses `uv run`.
    ///
    /// MUST stay in lockstep with `bundled_services` and `scripts/dev-local.sh`.
    fn dev_services(project_root: &str) -> Vec<ServiceDef> {
        vec![
            // Order 0: SQLite Proxy — see bundled_services for rationale.
            ServiceDef {
                id: "sqlite_proxy".to_string(),
                label: "SQLite Proxy".to_string(),
                command: "uv".to_string(),
                args: vec![
                    "run".to_string(),
                    "python".to_string(),
                    "-m".to_string(),
                    "xyz_agent_context.utils.sqlite_proxy_server".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: Some(8100),
                health_url: None,
                order: 0,
                startup_delay_ms: Some(3000),
            },
            ServiceDef {
                id: "backend".to_string(),
                label: "Backend API".to_string(),
                command: "uv".to_string(),
                // Dashboard v2 TDR-12 + ws-ping rationale: see bundled_services().
                args: vec![
                    "run".to_string(),
                    "uvicorn".to_string(),
                    "backend.main:app".to_string(),
                    "--host".to_string(),
                    "127.0.0.1".to_string(),
                    "--port".to_string(),
                    "8000".to_string(),
                    "--ws-ping-interval".to_string(),
                    "30".to_string(),
                    "--ws-ping-timeout".to_string(),
                    "60".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: Some(8000),
                health_url: Some("/docs".to_string()),
                order: 1,
                startup_delay_ms: None,
            },
            ServiceDef {
                id: "mcp".to_string(),
                label: "MCP Server".to_string(),
                command: "uv".to_string(),
                args: vec![
                    "run".to_string(),
                    "python".to_string(),
                    "src/xyz_agent_context/module/module_runner.py".to_string(),
                    "mcp".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 2,
                startup_delay_ms: None,
            },
            ServiceDef {
                id: "poller".to_string(),
                label: "Module Poller".to_string(),
                command: "uv".to_string(),
                args: vec![
                    "run".to_string(),
                    "python".to_string(),
                    "-m".to_string(),
                    "xyz_agent_context.services.module_poller".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 3,
                startup_delay_ms: None,
            },
            ServiceDef {
                id: "job_trigger".to_string(),
                label: "Job Trigger".to_string(),
                command: "uv".to_string(),
                args: vec![
                    "run".to_string(),
                    "python".to_string(),
                    "src/xyz_agent_context/module/job_module/job_trigger.py".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 4,
                startup_delay_ms: None,
            },
            ServiceDef {
                id: "message_bus_trigger".to_string(),
                label: "Bus Trigger".to_string(),
                command: "uv".to_string(),
                args: vec![
                    "run".to_string(),
                    "python".to_string(),
                    "-m".to_string(),
                    "xyz_agent_context.message_bus.message_bus_trigger".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 5,
                startup_delay_ms: None,
            },
            // Consolidated IM channel triggers — one supervisor process runs
            // every channel (Lark / Slack / Telegram / Discord / WeChat /
            // NarraMessenger) in a single event loop, replacing the old
            // one-ServiceDef-per-channel layout.
            ServiceDef {
                id: "channel_triggers".to_string(),
                label: "Channel Triggers".to_string(),
                command: "uv".to_string(),
                args: vec![
                    "run".to_string(),
                    "python".to_string(),
                    "-m".to_string(),
                    "xyz_agent_context.module.run_channel_triggers".to_string(),
                ],
                cwd: Some(project_root.to_string()),
                port: None,
                health_url: None,
                order: 6,
                startup_delay_ms: None,
            },
        ]
    }
}

pub struct AppState {
    pub config: Mutex<AppConfig>,
    pub process_manager: Arc<Mutex<ProcessManager>>,
    pub health_monitor: Arc<HealthMonitor>,
    pub service_defs: Vec<ServiceDef>,
    /// Dashboard v2 (TDR-7): holds the TrayIcon created in `lib.rs::setup`
    /// so that `commands::tray::set_tray_badge` can update its title.
    ///
    /// std::sync::Mutex (not tokio) because tray ops are sync + do not span
    /// await boundaries. Set once at startup, rarely modified after.
    pub tray_handle: Arc<StdMutex<Option<TrayIcon>>>,
    /// PID of the in-flight `claude auth login` child, if any.
    ///
    /// Written by `commands::auth::trigger_claude_login` right after spawn,
    /// cleared in its `finally` path. Read by `cancel_claude_login` so the
    /// frontend's 600s countdown (or a future explicit Cancel button) can
    /// SIGTERM the child without holding the Child handle across awaits.
    /// `None` means no login flow is currently running.
    pub claude_login_pid: Arc<StdMutex<Option<u32>>>,
    /// Latest `narranexus://` URL the OS handed to this process, buffered
    /// for the frontend to drain on mount. See
    /// `commands::deep_link::consume_pending_deep_link` for why this is
    /// needed alongside the live Tauri event (cold-start race: URLs
    /// arrive in Rust before React mounts a listener).
    pub pending_deep_link: Arc<StdMutex<Option<String>>>,
    /// Single source of truth for the auto-updater state machine. All three
    /// entry points (startup auto-check, tray "Check for Updates…", Settings
    /// page button) mutate this same field; the UI surfaces (global banner,
    /// Settings panel, tray menu label) all mirror it via the `updater:state`
    /// Tauri event. See `commands::updater` for the pipeline.
    pub updater_state: Arc<StdMutex<crate::commands::updater::UpdaterState>>,
}

impl Default for AppState {
    fn default() -> Self {
        let project_root = resolve_project_root();
        let python_path = resolve_python_path();
        let bundled = is_bundled();

        let project_root_str = project_root.to_string_lossy().to_string();
        let python_path_str = python_path.to_string_lossy().to_string();

        Self {
            config: Mutex::new(AppConfig {
                db_path: Some(resolve_db_path().to_string_lossy().to_string()),
                python_path: Some(python_path_str.clone()),
                ..AppConfig::default()
            }),
            process_manager: Arc::new(Mutex::new(ProcessManager::new())),
            health_monitor: Arc::new(HealthMonitor::new()),
            service_defs: ServiceDef::default_services(&project_root_str, &python_path_str, bundled),
            tray_handle: Arc::new(StdMutex::new(None)),
            claude_login_pid: Arc::new(StdMutex::new(None)),
            pending_deep_link: Arc::new(StdMutex::new(None)),
            updater_state: Arc::new(StdMutex::new(
                crate::commands::updater::UpdaterState::Idle,
            )),
        }
    }
}
