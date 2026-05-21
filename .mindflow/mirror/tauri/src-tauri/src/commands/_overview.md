---
code_dir: tauri/src-tauri/src/commands/
last_verified: 2026-05-18
---

# commands/ — IPC command handlers exposed to the frontend via Tauri

All `#[tauri::command]` functions live here. The frontend calls these via
`@tauri-apps/api/core`'s `invoke()` (or our shim in `frontend/src/lib/tauri.ts`
that pokes `window.__TAURI_INTERNALS__.invoke` directly to avoid the npm
package dep). Registered in `lib.rs::invoke_handler`.

Modules:
- `service.rs` — process lifecycle (status, start all, stop all, restart one)
- `health.rs` — health check and log retrieval
- `config.rs` — app config and mode (local / cloud-app)
- `tray.rs` — tray badge counter
- `auth.rs` — Claude Code OAuth login/logout/status (spawns CLI)
- `deep_link.rs` — drains `narranexus://` URLs the OS handed the process
  before the React listener mounted (cold-start race buffer; see
  `lib.rs.md` 2026-05-18 dated entry)

All commands take `state: State<'_, AppState>` for access to the shared
process manager, health monitor, and config.
