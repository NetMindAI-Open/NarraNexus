// Tauri command exposing the most recent `narranexus://` URL the OS
// delivered to this process, so the frontend can pick it up on first
// mount.
//
// Why this exists: `tauri-plugin-deep-link::on_open_url` fires from Rust
// the moment a URL arrives — including during cold start, well before
// React mounts and registers its `deep-link-received` event listener.
// Events fired before any listener exists are dropped on the floor. So
// the Rust handler ALSO stashes the URL in `AppState::pending_deep_link`,
// and the frontend's `useEffect` calls this command exactly once on
// mount to drain it.
//
// `take()` clears the slot — repeat calls return `None`. Hot URLs
// (arriving while the app is already running) go through the event
// channel as normal, since the frontend listener is then already set up.

use crate::state::AppState;

#[tauri::command]
pub async fn consume_pending_deep_link(
    state: tauri::State<'_, AppState>,
) -> Result<Option<String>, String> {
    let mut guard = state
        .pending_deep_link
        .lock()
        .map_err(|e| format!("pending_deep_link mutex poisoned: {}", e))?;
    Ok(guard.take())
}
