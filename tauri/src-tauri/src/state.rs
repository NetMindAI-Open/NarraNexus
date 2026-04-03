use serde::{Deserialize, Serialize};
use std::sync::Arc;
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
}

impl ServiceDef {
    pub fn default_services() -> Vec<ServiceDef> {
        vec![
            ServiceDef {
                id: "backend".to_string(),
                label: "Backend API".to_string(),
                command: "uv".to_string(),
                args: vec![
                    "run".to_string(),
                    "uvicorn".to_string(),
                    "backend.main:app".to_string(),
                    "--port".to_string(),
                    "8000".to_string(),
                ],
                cwd: None,
                port: Some(8000),
                health_url: Some("/docs".to_string()),
                order: 1,
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
                cwd: None,
                port: None,
                health_url: None,
                order: 2,
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
                cwd: None,
                port: None,
                health_url: None,
                order: 3,
            },
        ]
    }
}

pub struct AppState {
    pub config: Mutex<AppConfig>,
    pub process_manager: Mutex<ProcessManager>,
    pub health_monitor: Arc<HealthMonitor>,
    pub service_defs: Vec<ServiceDef>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            config: Mutex::new(AppConfig::default()),
            process_manager: Mutex::new(ProcessManager::new()),
            health_monitor: Arc::new(HealthMonitor::new()),
            service_defs: ServiceDef::default_services(),
        }
    }
}
