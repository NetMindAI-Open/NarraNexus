use tauri::State;

use crate::state::{AppConfig, AppMode, AppState};

#[tauri::command]
pub async fn get_app_config(state: State<'_, AppState>) -> Result<AppConfig, String> {
    let config = state.config.lock().await;
    Ok(config.clone())
}

#[tauri::command]
pub async fn get_app_mode(state: State<'_, AppState>) -> Result<String, String> {
    let config = state.config.lock().await;
    match config.mode {
        AppMode::Local => Ok("local".to_string()),
        AppMode::CloudApp => Ok("cloud-app".to_string()),
    }
}

#[tauri::command]
pub async fn set_app_mode(mode: String, state: State<'_, AppState>) -> Result<(), String> {
    let mut config = state.config.lock().await;
    config.mode = match mode.as_str() {
        "local" => AppMode::Local,
        "cloud-app" => AppMode::CloudApp,
        _ => return Err(format!("Invalid mode: {}", mode)),
    };
    Ok(())
}
