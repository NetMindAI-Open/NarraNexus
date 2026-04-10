---
code_file: tauri/src-tauri/src/state.rs
last_verified: 2026-04-10
---

# state.rs — AppState, ServiceDef, and path resolution for the Tauri app

The central configuration file for the desktop app. Defines:

- `AppConfig` — mode (local/cloud), api_base_url, python_path, db_path
- `ServiceDef` — one per child process (id, label, command, args, cwd, port,
  health_url, order, startup_delay_ms)
- `AppState` — Tauri managed state holding config, process_manager,
  health_monitor, and service_defs
- Path resolution helpers: `resolve_resource_dir`, `resolve_project_root`,
  `resolve_python_path`, `resolve_db_path`, `is_bundled`

## Why path resolution is non-trivial

A macOS `.app` bundle has a specific directory layout:
```
NarraNexus.app/
  Contents/
    MacOS/narranexus        ← executable
    Resources/resources/
      project/              ← Python project root
      python/bin/python3    ← bundled Python
```

Development layout:
```
tauri/src-tauri/     ← CWD during dev
../../              ← project root (two levels up)
uv                  ← Python via PATH
```

`is_bundled()` detects which layout is active by checking for the bundled
Python path. The service factories (`bundled_services` vs `dev_services`)
choose the right commands based on this.

## The two service factories

`bundled_services` uses the absolute Python path directly (no `uv`).
`dev_services` prefixes all commands with `uv run python ...` for the
virtual-env-managed dev workflow.

Both factories define the same six services in the same order:
1. sqlite_proxy (order 0, 3 s startup delay)
2. backend (order 1)
3. mcp (order 2)
4. poller (order 3)
5. job_trigger (order 4)
6. message_bus_trigger (order 5)

**These MUST stay in sync with `scripts/dev-local.sh` (CLAUDE.md rule #7).**

## The SQLite proxy startup delay

Order 0 (sqlite_proxy) has `startup_delay_ms: Some(3000)`. This mirrors the
`sleep 3` in `scripts/dev-local.sh`. Without this delay, backend/mcp/poller
try to connect to the proxy before it binds port 8100 and crash. The value
was chosen empirically — on slow machines 3 s may not be enough.

## Gotchas

`resolve_db_path` always uses `~/.narranexus/nexus.db` regardless of mode.
There is no per-user or per-environment isolation. Running two agents
simultaneously from different installations shares the same database.
