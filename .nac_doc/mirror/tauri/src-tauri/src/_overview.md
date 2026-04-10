---
code_dir: tauri/src-tauri/src/
last_verified: 2026-04-10
---

# src/ — Tauri desktop shell: Python sidecar management and IPC commands

The Tauri Rust layer wraps the NexusAgent Python backend stack in a desktop
app. Its job is to:

1. Spawn and manage all Python child processes (SQLite proxy, FastAPI backend,
   MCP server, Module Poller, Job Trigger, Message Bus Trigger)
2. Expose IPC commands to the frontend (via `tauri::command`)
3. Monitor service health via TCP port checks
4. Collect stdout/stderr from children into a ring buffer for the LogViewer
5. Provide a system tray icon

## Module map

```
lib.rs          ← Tauri app bootstrap: setup(), window close, IPC handler registration
main.rs         ← entry point, delegates to lib::run()
state.rs        ← AppState, ServiceDef, path resolution helpers
tray.rs         ← system tray icon and menu
sidecar/
  process_manager.rs  ← spawn, kill, restart children; log drainer tasks
  health_monitor.rs   ← TCP port check with debounce
  mod.rs              ← pub re-exports
commands/
  service.rs    ← IPC: get_service_status, start/stop/restart
  health.rs     ← IPC: get_health_status, get_logs
  config.rs     ← IPC: get_app_config, get/set_app_mode
  mod.rs        ← pub re-exports
```

## The core problem this solves

Python does not have a native GUI framework that matches web frontend quality.
Tauri lets the existing React frontend run as a desktop app while Rust manages
the child process lifecycle reliably, surviving macOS sandbox restrictions and
codesign requirements.

## Iron rule alignment (CLAUDE.md rule #7)

Every service definition in `state.rs` (`bundled_services`, `dev_services`)
MUST stay in lockstep with `scripts/dev-local.sh`. Order, commands, delays —
all must match. Breaking this parity is a latent bug that only manifests in
the packaged `.dmg`.

## Key historical bugs fixed

- **Pipe deadlock (5cf8c1d):** Python sidecars write to loguru (stderr by
  default). If Tauri never reads the pipe, the 16 KB kernel buffer fills and
  the child blocks on its next `write(stderr)` — making the agent loop hang
  at step 3.2 with no visible error. Fixed by `spawn_log_drainer` tasks.
- **Environment var inheritance (acb7723):** `std::env::set_var` in
  `lib.rs::setup()` is not thread-safe on macOS. The tokio spawner thread may
  not observe the write, so children inherited empty `DATABASE_URL` and
  treated it as cloud mode. Fixed by explicitly passing env vars via
  `.env("DATABASE_URL", ...)` in `start_service`.
