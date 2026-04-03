use tauri::State;

use crate::sidecar::health_monitor::{OverallHealth, ServiceHealth};
use crate::sidecar::process_manager::LogEntry;
use crate::state::AppState;

#[tauri::command]
pub async fn get_health_status(state: State<'_, AppState>) -> Result<OverallHealth, String> {
    let mut services = Vec::new();

    for def in &state.service_defs {
        let health_state = state.health_monitor.check_service(&def.id, def.port).await;
        services.push(ServiceHealth {
            service_id: def.id.clone(),
            label: def.label.clone(),
            state: health_state,
            port: def.port,
        });
    }

    let all_healthy = services.iter().all(|s| {
        s.port.is_none()
            || matches!(
                s.state,
                crate::sidecar::health_monitor::HealthState::Healthy
            )
    });

    Ok(OverallHealth {
        services,
        all_healthy,
    })
}

#[tauri::command]
pub async fn get_logs(
    service_id: Option<String>,
    state: State<'_, AppState>,
) -> Result<Vec<LogEntry>, String> {
    let pm = state.process_manager.lock().await;
    Ok(pm.get_logs(service_id.as_deref()))
}
