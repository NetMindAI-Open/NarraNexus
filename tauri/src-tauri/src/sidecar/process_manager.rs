use log;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::process::Stdio;
use std::sync::{Arc, Mutex as StdMutex};
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::process::{Child, Command};

use crate::state::ServiceDef;

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

        // Dashboard v2 TDR-12: for the backend service, export DASHBOARD_BIND_HOST
        // as a redundant signal to the lifespan bind assertion. The uvicorn CLI
        // `--host 127.0.0.1` is already set in ServiceDef.args; this env var is
        // a defense-in-depth that survives even if args are ever edited.
        let mut cmd = Command::new(&def.command);
        cmd.args(&def.args)
            .current_dir(&cwd)
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

    pub async fn stop_service(&mut self, service_id: &str) -> Result<(), String> {
        if let Some(mut child) = self.processes.remove(service_id) {
            log::info!("Stopping service: {}", service_id);
            child
                .kill()
                .await
                .map_err(|e| format!("Failed to stop {}: {}", service_id, e))?;
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

/// Spawn a detached tokio task that reads one line at a time from a child's
/// piped stdout/stderr and appends it to the shared log buffer.
///
/// Why this exists: `tokio::process::Command` with `Stdio::piped()` creates
/// an OS pipe that MUST be drained. If nothing reads the parent end, the
/// kernel buffer fills up (~16KB on macOS) and the child's next write to
/// that fd blocks — deadlocking the Python sidecars mid-execution and
/// making the frontend agent loop look "stuck" for no visible reason.
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
                        message: line,
                    };
                    if let Ok(mut buf) = logs.lock() {
                        if buf.len() >= max_logs {
                            buf.pop_front();
                        }
                        buf.push_back(entry);
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
