---
code_dir: tauri/src-tauri/src/sidecar/
last_verified: 2026-04-10
---

# sidecar/ — Python child process management and health monitoring

Two modules:

- `process_manager.rs` — spawn, stop, restart child processes; collect their
  stdout/stderr into a shared ring buffer
- `health_monitor.rs` — TCP port reachability check with debounce

The word "sidecar" here is used loosely (not Tauri's official sidecar feature
for bundled binaries). These are just `tokio::process::Command`-spawned
children, managed manually.

## Why not use Tauri's native sidecar plugin

Tauri's official sidecar requires code-signing each bundled binary separately.
The Python services are not separate binaries — they are Python scripts run
via the bundled Python interpreter. Manual process management gives more
control over env vars, cwd, and the startup order/delay logic that mirrors
`dev-local.sh`.
