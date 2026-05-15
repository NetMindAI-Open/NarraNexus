---
code_file: tauri/src-tauri/src/lib.rs
last_verified: 2026-05-06
---

# lib.rs — Tauri app bootstrap: registers commands, wires setup, handles close

The single `run()` function that `main.rs` delegates to. This is where all
Tauri builder configuration lives.

## What happens at startup

1. `AppState::default()` resolves paths, detects bundled vs dev mode,
   creates `ServiceDef` list.
2. `setup()` callback:
   - Runs `sidecar::port_preflight::check_required_ports()` FIRST — if any
     required port (8000 / 8100 / 7801 / 7830) is held by another process,
     show a native `osascript` dialog and exit. This prevents the
     "black screen forever" UX when a user has another backend on :8000.
   - Sets `DATABASE_URL` env var pointing to `~/.narranexus/nexus.db`
   - Sets `SQLITE_PROXY_URL=http://localhost:8100` and `SQLITE_PROXY_PORT=8100`
   - Creates the system tray
   - Fires `sidecar::lark_preflight::run_preflight()` — detached best-effort
     task that installs `@larksuite/cli` and its skill pack if missing
     (mirrors `scripts/run.sh` `check_deps`). Failures never block startup.
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

## Registered IPC commands

The `invoke_handler!` macro registers all frontend-callable commands.
Current set (kept in lockstep with `commands/mod.rs`):

- service: `get_service_status`, `start_all_services`, `stop_all_services`, `restart_service`
- config:  `get_app_config`, `get_app_mode`, `set_app_mode`
- health:  `get_health_status`, `get_logs`
- tray:    `set_tray_badge`
- auth:    `trigger_claude_login`, `trigger_claude_logout`, `cancel_claude_login`, `get_claude_login_status`

Forgetting to add a freshly-defined command here is the #1 frontend symptom
("invoke returned 'command not found'") — the macro list is the source of
truth. `commands/auth.rs` exists since 2026-04-30 (in-app Claude Code OAuth).

## Upstream / downstream

- **Called by:** `main.rs`
- **Depends on:** `state`, `sidecar`, `tray`, `commands` modules

## Single-instance + unified shutdown

Two pieces of plumbing keep the desktop app from stepping on its own toes
across relaunches:

- **`tauri-plugin-single-instance`** — registered FIRST (before any
  other plugin or setup work). When a second `narranexus` process
  launches, it forwards argv to the live process via the plugin's IPC
  channel and exits non-zero before any sidecar spawn. The first
  process's init() callback raises/focuses the existing window. This
  is what stops "double-click the .app twice fast → port already in
  use" from happening.

- **`RunEvent::ExitRequested` is the single chokepoint** for stopping
  sidecars. Every exit path Tauri exposes — Cmd+Q, tray Quit
  (`app.exit(0)`), Dock quit, system logout/shutdown — fires this
  event before the runtime tears down. The handler block_on's
  `pm.stop_all()` exactly once. Previously the cleanup was wired only
  to `WindowEvent::CloseRequested`, so any non-window-close exit (the
  tray menu being the obvious one) bypassed the cleanup and left
  Python sidecars holding 8000/8100/7801/7830 as orphans. Today
  CloseRequested still logs but defers stopping to ExitRequested.

The structural change: switched from terminal `.run(context)` to
`.build(context).run(closure)` so the closure can match on RunEvent.

## Gotchas

`on_window_event` and the ExitRequested handler must NOT both stop
services; we deliberately let CloseRequested only log. If you ever
add stop logic back to CloseRequested, account for the second pass
in ExitRequested running on an empty processes map (it's currently
idempotent, so it's fine — but worth knowing).

`tauri::async_runtime::block_on` inside ExitRequested is intentional:
the runtime is still alive at that point, the work is short
(per-child SIGTERM + 3s wait + optional SIGKILL), and we MUST
complete before the runtime drops the process manager.
