use log;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::process::Stdio;
use tokio::process::{Child, Command};

use crate::state::ServiceDef;

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
    logs: VecDeque<LogEntry>,
    max_logs: usize,
}

impl ProcessManager {
    pub fn new() -> Self {
        Self {
            processes: HashMap::new(),
            status: HashMap::new(),
            logs: VecDeque::new(),
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

        let child = Command::new(&def.command)
            .args(&def.args)
            .current_dir(&cwd)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| format!("Failed to start {}: {}", def.id, e))?;

        let pid = child.id();

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
        match service_id {
            Some(id) => self
                .logs
                .iter()
                .filter(|l| l.service_id == id)
                .cloned()
                .collect(),
            None => self.logs.iter().cloned().collect(),
        }
    }

    pub fn add_log(&mut self, entry: LogEntry) {
        if self.logs.len() >= self.max_logs {
            self.logs.pop_front();
        }
        self.logs.push_back(entry);
    }

    pub fn promote_to_running(&mut self, service_id: &str) {
        if let Some(info) = self.status.get_mut(service_id) {
            if matches!(info.status, ServiceStatus::Starting) {
                info.status = ServiceStatus::Running;
            }
        }
    }
}
