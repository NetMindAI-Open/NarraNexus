use log;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use tokio::net::TcpStream;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum HealthState {
    Unknown,
    Healthy,
    Unhealthy,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServiceHealth {
    pub service_id: String,
    pub label: String,
    pub state: HealthState,
    pub port: Option<u16>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OverallHealth {
    pub services: Vec<ServiceHealth>,
    pub all_healthy: bool,
}

pub struct HealthMonitor {
    check_interval_secs: u64,
    debounce_threshold: u32,
    unhealthy_counts: tokio::sync::Mutex<HashMap<String, u32>>,
}

impl HealthMonitor {
    pub fn new() -> Self {
        Self {
            check_interval_secs: 5,
            debounce_threshold: 2,
            unhealthy_counts: tokio::sync::Mutex::new(HashMap::new()),
        }
    }

    pub async fn check_port(port: u16) -> bool {
        let addr = format!("127.0.0.1:{}", port);
        TcpStream::connect(&addr).await.is_ok()
    }

    pub async fn check_service(&self, service_id: &str, port: Option<u16>) -> HealthState {
        let port = match port {
            Some(p) => p,
            None => return HealthState::Unknown,
        };

        let reachable = Self::check_port(port).await;

        let mut counts = self.unhealthy_counts.lock().await;
        if reachable {
            counts.remove(service_id);
            HealthState::Healthy
        } else {
            let count = counts.entry(service_id.to_string()).or_insert(0);
            *count += 1;
            if *count >= self.debounce_threshold {
                HealthState::Unhealthy
            } else {
                HealthState::Unknown
            }
        }
    }
}
