use tauri::State;

use crate::sidecar::process_manager::ProcessInfo;
use crate::state::AppState;

#[tauri::command]
pub async fn get_service_status(state: State<'_, AppState>) -> Result<Vec<ProcessInfo>, String> {
    let pm = state.process_manager.lock().await;
    Ok(pm.get_all_status())
}

#[tauri::command]
pub async fn start_all_services(state: State<'_, AppState>) -> Result<(), String> {
    let mut pm = state.process_manager.lock().await;
    let defs = state.service_defs.clone();
    // TODO: resolve project_root from app resource path
    let project_root = ".";
    pm.start_all(&defs, project_root).await
}

#[tauri::command]
pub async fn stop_all_services(state: State<'_, AppState>) -> Result<(), String> {
    let mut pm = state.process_manager.lock().await;
    pm.stop_all().await;
    Ok(())
}

#[tauri::command]
pub async fn restart_service(
    service_id: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let def = state
        .service_defs
        .iter()
        .find(|d| d.id == service_id)
        .cloned()
        .ok_or_else(|| format!("Service '{}' not found", service_id))?;
    let mut pm = state.process_manager.lock().await;
    let project_root = ".";
    pm.restart_service(&def, project_root).await
}
