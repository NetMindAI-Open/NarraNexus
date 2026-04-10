---
code_file: tauri/src-tauri/src/lib.rs
last_verified: 2026-04-10
---

# lib.rs — Tauri app bootstrap: registers commands, wires setup, handles close

The single `run()` function that `main.rs` delegates to. This is where all
Tauri builder configuration lives.

## What happens at startup

1. `AppState::default()` resolves paths, detects bundled vs dev mode,
   creates `ServiceDef` list.
2. `setup()` callback:
   - Sets `DATABASE_URL` env var pointing to `~/.narranexus/nexus.db`
   - Sets `SQLITE_PROXY_URL=http://localhost:8100` and `SQLITE_PROXY_PORT=8100`
   - Creates the system tray
   - Spawns `pm.start_all(&defs, &project_root_str)` as a detached tokio task
3. `on_window_event` CloseRequested: calls `pm.stop_all()` synchronously on
   a new tokio Runtime (blocking, so all child processes are killed before
   the process exits)

## Critical env var setup

```rust
std::env::set_var("DATABASE_URL", format!("sqlite:///{}", db_path.display()));
std::env::set_var("SQLITE_PROXY_URL", "http://localhost:8100");
std::env::set_var("SQLITE_PROXY_PORT", "8100");
```

These are set here but **not reliably inherited** by spawned children due to
macOS thread-safety issues. `process_manager.rs::start_service` re-reads them
and passes them explicitly via `.env(...)`. Both placements are required.

## Upstream / downstream

- **Called by:** `main.rs`
- **Depends on:** `state`, `sidecar`, `tray`, `commands` modules

## Gotchas

`on_window_event` uses `tokio::runtime::Runtime::new()` to block on
`stop_all`. This creates a second tokio runtime which is unusual and only
safe because the async work is brief (SIGKILL each child). Do not add
long-running async work here.
