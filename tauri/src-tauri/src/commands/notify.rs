//! @file_name: notify.rs
//! @description: Tauri command `notify_completion` — post an OS-level
//! notification when an agent finishes its reply (#44).
//!
//! Contract with frontend (`frontend/src/lib/desktopNotify.ts`):
//! - invoked only from the desktop build, only when the window is unfocused
//!   and the run was not user-cancelled (that gating lives in chatStore).
//! - best-effort: a failure (notification permission denied, etc.) returns
//!   Err, which the frontend swallows — a notification must never break chat.
//!
//! Uses the notification plugin's Rust API instead of its JS guest bindings
//! because the frontend carries no `@tauri-apps/*` npm dependency (bare
//! specifiers break inside the packaged DMG — see frontend/src/lib/tauri.ts).

use tauri_plugin_notification::NotificationExt;

#[tauri::command]
pub fn notify_completion(app: tauri::AppHandle, title: String, body: String) -> Result<(), String> {
    app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show()
        .map_err(|e| e.to_string())
}
