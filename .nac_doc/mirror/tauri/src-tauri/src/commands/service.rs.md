---
code_file: tauri/src-tauri/src/commands/service.rs
last_verified: 2026-04-10
---

# service.rs — IPC commands for process lifecycle management

Four commands:
- `get_service_status` → `Vec<ProcessInfo>` (all services' current status)
- `start_all_services` → spawns all services (idempotent if already running?)
- `stop_all_services` → kills all children
- `restart_service(service_id)` → stop + 1 s delay + start one service

`restart_service` looks up the `ServiceDef` by `service_id` from
`state.service_defs`. If the ID doesn't match any definition, returns an
error string.

Used by: System page's restart buttons and (planned) tray menu actions.
