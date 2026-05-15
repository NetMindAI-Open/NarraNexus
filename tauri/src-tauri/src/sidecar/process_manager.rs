use chrono::Local;
use log;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::{Arc, Mutex as StdMutex};
use tokio::fs::OpenOptions;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};

use crate::state::{resolve_bundled_node_bins, ServiceDef};

/// Ring buffer shared between ProcessManager and the per-child log drainer
/// tasks. Uses std::sync::Mutex (not tokio's) because log pushes are brief
/// and never cross .await points — this keeps log writes decoupled from the
/// outer tokio::sync::Mutex<ProcessManager> so start_all can hold the outer
/// lock without blocking drainers.
type LogBuffer = Arc<StdMutex<VecDeque<LogEntry>>>;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ServiceStatus {
    Stopped,
    Starting,
    Running,
    Crashed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProcessInfo {
    pub service_id: String,
    pub label: String,
    pub status: ServiceStatus,
    pub pid: Option<u32>,
    pub restart_count: u32,
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LogEntry {
    pub service_id: String,
    pub timestamp: u64,
    pub stream: String,
    pub message: String,
}

pub struct ProcessManager {
    processes: HashMap<String, Child>,
    status: HashMap<String, ProcessInfo>,
    logs: LogBuffer,
    max_logs: usize,
}

impl ProcessManager {
    pub fn new() -> Self {
        Self {
            processes: HashMap::new(),
            status: HashMap::new(),
            logs: Arc::new(StdMutex::new(VecDeque::new())),
            max_logs: 500,
        }
    }

    pub async fn start_service(
        &mut self,
        def: &ServiceDef,
        project_root: &str,
    ) -> Result<(), String> {
        log::info!("Starting service: {} ({})", def.label, def.id);

        let cwd = def
            .cwd
            .clone()
            .unwrap_or_else(|| project_root.to_string());

        // Explicitly propagate DATABASE_URL / SQLITE_PROXY_URL / SQLITE_PROXY_PORT
        // to the child process.
        //
        // Tauri's lib.rs setup() calls std::env::set_var(...) to point the
        // bundled Python backend at the per-user SQLite file and to tell
        // every service to talk to the SQLite proxy. However,
        // std::env::set_var is NOT thread-safe on macOS — the tokio thread
        // that spawns this subprocess may not observe the write, and the
        // child then inherits an empty value. The Python side historically
        // treated empty DATABASE_URL as "cloud mode", which made the bundled
        // desktop app demand passwords in local mode.
        //
        // Reading each var here and passing it via .env() bypasses the
        // implicit inheritance path and makes the intent fully explicit.
        // If a var is unset here too, we pass an empty string and rely on
        // the Python-side defaults.
        //
        // SQLITE_PROXY_URL is especially load-bearing: without it, every
        // child process (backend, mcp, poller, triggers) falls back to
        // opening the SQLite file directly, which causes multi-process lock
        // contention and hangs the agent loop the moment chat starts.
        let db_url = std::env::var("DATABASE_URL").unwrap_or_default();
        let proxy_url = std::env::var("SQLITE_PROXY_URL").unwrap_or_default();
        let proxy_port = std::env::var("SQLITE_PROXY_PORT").unwrap_or_default();

        // Prepend bundled Node.js + CLI shim paths to PATH.
        //
        // Why: claude_agent_sdk (Python) spawns the `claude` binary via
        // shutil.which / subprocess without a full path. Finder-launched
        // .app inherits launchd's minimal PATH (`/usr/bin:/bin:/usr/sbin:
        // /sbin`) which never contains claude. Without this injection
        // every chat turn blows up with "No such file or directory: 'claude'".
        //
        // In dev mode (non-bundled) resolve_bundled_node_bins() returns empty
        // and we leave PATH alone — dev users already have node + the CLIs on
        // their shell PATH via the `uv run` wrapper.
        let parent_path = std::env::var("PATH").unwrap_or_default();
        let bundled_bins = resolve_bundled_node_bins();
        let child_path = if bundled_bins.is_empty() {
            parent_path.clone()
        } else {
            let mut parts: Vec<String> = bundled_bins
                .iter()
                .map(|p| p.to_string_lossy().to_string())
                .collect();
            if !parent_path.is_empty() {
                parts.push(parent_path.clone());
            }
            parts.join(":")
        };

        // Dashboard v2 TDR-12: for the backend service, export DASHBOARD_BIND_HOST
        // as a redundant signal to the lifespan bind assertion. The uvicorn CLI
        // `--host 127.0.0.1` is already set in ServiceDef.args; this env var is
        // a defense-in-depth that survives even if args are ever edited.
        let mut cmd = Command::new(&def.command);
        cmd.args(&def.args)
            .current_dir(&cwd)
            .env("PATH", &child_path)
            .env("DATABASE_URL", &db_url)
            .env("SQLITE_PROXY_URL", &proxy_url)
            .env("SQLITE_PROXY_PORT", &proxy_port);
        if def.id == "backend" {
            cmd.env("DASHBOARD_BIND_HOST", "127.0.0.1");
        }
        let mut child = cmd
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| format!("Failed to start {}: {}", def.id, e))?;

        let pid = child.id();

        // CRITICAL: drain the child's stdout/stderr into the shared log
        // buffer. If nobody reads these pipes, the kernel buffer (≈16KB on
        // macOS) fills up and the child blocks on its next write(stderr).
        // loguru's default handler writes to stderr, so every Python
        // sidecar would silently deadlock mid-run once the buffer fills —
        // this was the direct cause of `chat hangs at agent loop step 3.2`
        // on the packaged dmg. Draining also feeds the System page's
        // LogViewer so users can actually see what's happening.
        if let Some(stdout) = child.stdout.take() {
            spawn_log_drainer(
                def.id.clone(),
                "stdout",
                stdout,
                self.logs.clone(),
                self.max_logs,
            );
        }
        if let Some(stderr) = child.stderr.take() {
            spawn_log_drainer(
                def.id.clone(),
                "stderr",
                stderr,
                self.logs.clone(),
                self.max_logs,
            );
        }

        self.status.insert(
            def.id.clone(),
            ProcessInfo {
                service_id: def.id.clone(),
                label: def.label.clone(),
                status: ServiceStatus::Starting,
                pid,
                restart_count: 0,
                last_error: None,
            },
        );

        self.processes.insert(def.id.clone(), child);
        log::info!("Service {} started with PID {:?}", def.id, pid);

        Ok(())
    }

    pub async fn start_all(
        &mut self,
        defs: &[ServiceDef],
        project_root: &str,
    ) -> Result<(), String> {
        let mut sorted_defs = defs.to_vec();
        sorted_defs.sort_by_key(|d| d.order);

        for def in &sorted_defs {
            self.start_service(def, project_root).await?;
            // Mirror `scripts/dev-local.sh`'s `sleep 3` after
            // sqlite_proxy_server: give a service time to come up before
            // dependents start, when requested via ServiceDef.
            if let Some(delay_ms) = def.startup_delay_ms {
                log::info!(
                    "Waiting {}ms for {} to become ready before starting next service",
                    delay_ms,
                    def.id
                );
                tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
            }
        }
        Ok(())
    }

    /// Stop a service gracefully:
    ///   1. SIGTERM — gives the Python child a chance to run its
    ///      `try/except KeyboardInterrupt` / `finally` blocks, await
    ///      `trigger.stop()`, flush loguru's enqueue=True buffers,
    ///      release DB connections, close sockets cleanly. Without
    ///      this, ports often linger in TIME_WAIT and the next launch
    ///      hits "address already in use" even though our own state
    ///      thinks everything is stopped.
    ///   2. Wait up to 3s for the child to exit on its own.
    ///   3. SIGKILL fallback if the child ignored SIGTERM. tokio's
    ///      `Child::kill` is SIGKILL on Unix.
    pub async fn stop_service(&mut self, service_id: &str) -> Result<(), String> {
        if let Some(mut child) = self.processes.remove(service_id) {
            log::info!("Stopping service: {}", service_id);

            #[cfg(unix)]
            {
                if let Some(pid) = child.id() {
                    // Cast to i32 (libc::pid_t alias). Safety: kill() is
                    // safe to call with any pid; an invalid one just
                    // returns ESRCH which we ignore — child may already
                    // have exited between our remove() and this kill().
                    unsafe {
                        libc::kill(pid as i32, libc::SIGTERM);
                    }
                }
            }

            let graceful = tokio::time::timeout(
                std::time::Duration::from_secs(3),
                child.wait(),
            )
            .await;

            if graceful.is_err() {
                log::warn!(
                    "Service {} did not exit within 3s of SIGTERM — falling back to SIGKILL",
                    service_id
                );
                if let Err(e) = child.kill().await {
                    return Err(format!("Failed to SIGKILL {}: {}", service_id, e));
                }
            }

            if let Some(info) = self.status.get_mut(service_id) {
                info.status = ServiceStatus::Stopped;
                info.pid = None;
            }
        }
        Ok(())
    }

    pub async fn stop_all(&mut self) {
        let ids: Vec<String> = self.processes.keys().cloned().collect();
        for id in ids {
            if let Err(e) = self.stop_service(&id).await {
                log::error!("Error stopping {}: {}", id, e);
            }
        }
    }

    pub async fn restart_service(
        &mut self,
        def: &ServiceDef,
        project_root: &str,
    ) -> Result<(), String> {
        self.stop_service(&def.id).await?;
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        self.start_service(def, project_root).await
    }

    pub fn get_all_status(&self) -> Vec<ProcessInfo> {
        self.status.values().cloned().collect()
    }

    pub fn get_logs(&self, service_id: Option<&str>) -> Vec<LogEntry> {
        let Ok(logs) = self.logs.lock() else {
            return Vec::new();
        };
        match service_id {
            Some(id) => logs
                .iter()
                .filter(|l| l.service_id == id)
                .cloned()
                .collect(),
            None => logs.iter().cloned().collect(),
        }
    }

    pub fn promote_to_running(&mut self, service_id: &str) {
        if let Some(info) = self.status.get_mut(service_id) {
            if matches!(info.status, ServiceStatus::Starting) {
                info.status = ServiceStatus::Running;
            }
        }
    }
}

/// Resolve the on-disk log directory we share with the Python side.
/// Mirrors the layout used by setup_logging() in
/// src/xyz_agent_context/utils/logging/_setup.py — namely
/// ``$NEXUS_LOG_DIR/<service>/<service>_YYYYMMDD.log``. When neither
/// the env var nor a HOME directory is available we fall back to
/// `<tempdir>/narranexus-logs` so Tauri-only desktop runs still get
/// a stable location.
fn resolve_log_dir(service_id: &str) -> PathBuf {
    if let Ok(env_dir) = std::env::var("NEXUS_LOG_DIR") {
        if !env_dir.is_empty() {
            return PathBuf::from(env_dir).join(service_id);
        }
    }
    let base = dirs::home_dir().unwrap_or_else(|| std::env::temp_dir());
    base.join(".narranexus").join("logs").join(service_id)
}

fn current_log_path(service_id: &str) -> PathBuf {
    let date = Local::now().format("%Y%m%d").to_string();
    resolve_log_dir(service_id).join(format!("{service_id}_{date}.log"))
}

/// Spawn a detached tokio task that reads one line at a time from a child's
/// piped stdout/stderr and appends it to the shared log buffer AND to the
/// rotating file under ~/.narranexus/logs/<service>/.
///
/// Why this exists: `tokio::process::Command` with `Stdio::piped()` creates
/// an OS pipe that MUST be drained. If nothing reads the parent end, the
/// kernel buffer fills up (~16KB on macOS) and the child's next write to
/// that fd blocks — deadlocking the Python sidecars mid-execution and
/// making the frontend agent loop look "stuck" for no visible reason.
///
/// Why we also write to disk: the in-memory ring buffer is capped at 500
/// entries per service and is wiped when the desktop app exits. After the
/// log-system overhaul (M5/T18) the desktop app must place files in the
/// same `~/.narranexus/logs/` tree as the headless `bash run.sh` path so
/// either runtime is debuggable post-mortem (ironclad rule #7: dual run
/// modes aligned). The file path is recomputed per line so the daily
/// rollover happens implicitly when midnight crosses; we just append to
/// whatever YYYYMMDD file is current at that moment.
///
/// The task terminates naturally when the child closes its fd (EOF on the
/// pipe), so there's no explicit cleanup needed; child.kill_on_drop in
/// start_service takes care of the lifecycle.
fn spawn_log_drainer<R>(
    service_id: String,
    stream: &'static str,
    reader: R,
    logs: LogBuffer,
    max_logs: usize,
) where
    R: AsyncRead + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        // Ensure the per-service directory exists once at startup; later
        // writes can rely on it. Failure here only suppresses file
        // logging — the in-memory buffer still works.
        let log_dir = resolve_log_dir(&service_id);
        let dir_ready = match tokio::fs::create_dir_all(&log_dir).await {
            Ok(_) => true,
            Err(e) => {
                log::warn!(
                    "Could not create log dir {:?} for {}: {}",
                    log_dir,
                    service_id,
                    e
                );
                false
            }
        };

        let mut current_file_date = String::new();
        let mut current_file: Option<tokio::fs::File> = None;

        let mut lines = BufReader::new(reader).lines();
        loop {
            match lines.next_line().await {
                Ok(Some(line)) => {
                    let timestamp = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_secs())
                        .unwrap_or(0);
                    let entry = LogEntry {
                        service_id: service_id.clone(),
                        timestamp,
                        stream: stream.to_string(),
                        message: line.clone(),
                    };
                    if let Ok(mut buf) = logs.lock() {
                        if buf.len() >= max_logs {
                            buf.pop_front();
                        }
                        buf.push_back(entry);
                    }

                    if dir_ready {
                        let now_date = Local::now().format("%Y%m%d").to_string();
                        if now_date != current_file_date {
                            // Day rolled over (or first line) — open the new file.
                            let path = current_log_path(&service_id);
                            match OpenOptions::new()
                                .create(true)
                                .append(true)
                                .open(&path)
                                .await
                            {
                                Ok(f) => {
                                    current_file = Some(f);
                                    current_file_date = now_date;
                                }
                                Err(e) => {
                                    log::warn!(
                                        "Could not open log file {:?}: {}",
                                        path,
                                        e
                                    );
                                    current_file = None;
                                }
                            }
                        }
                        if let Some(file) = current_file.as_mut() {
                            // Format mirrors the Python text format so a
                            // grep against either source produces the
                            // same shape: "<HH:MM:SS> [stream] <line>".
                            let ts_str = Local::now().format("%H:%M:%S").to_string();
                            let written = file
                                .write_all(
                                    format!("{ts_str} [{stream}] {line}\n").as_bytes(),
                                )
                                .await;
                            if let Err(e) = written {
                                log::warn!(
                                    "Failed writing to log file for {}: {}",
                                    service_id,
                                    e
                                );
                                current_file = None;
                            }
                        }
                    }
                }
                Ok(None) => break, // EOF: child closed the pipe
                Err(e) => {
                    log::warn!(
                        "Log drainer for {} ({}) errored: {}",
                        service_id,
                        stream,
                        e
                    );
                    break;
                }
            }
        }
    });
}
