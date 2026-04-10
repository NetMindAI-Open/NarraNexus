---
code_dir: tauri/src-tauri/src/commands/
last_verified: 2026-04-10
---

# commands/ — IPC command handlers exposed to the frontend via Tauri

All `#[tauri::command]` functions live here. The frontend calls these via
`@tauri-apps/api/core`'s `invoke()`. Registered in `lib.rs::invoke_handler`.

Three modules:
- `service.rs` — process lifecycle (status, start all, stop all, restart one)
- `health.rs` — health check and log retrieval
- `config.rs` — app config and mode (local / cloud-app)

All commands take `state: State<'_, AppState>` for access to the shared
process manager, health monitor, and config.
