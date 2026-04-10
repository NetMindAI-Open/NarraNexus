---
code_file: tauri/src-tauri/src/commands/health.rs
last_verified: 2026-04-10
---

# health.rs — IPC commands for health status and log retrieval

Two commands:
- `get_health_status` → `OverallHealth` (calls `health_monitor.check_service`
  for each `ServiceDef`, aggregates `all_healthy` flag)
- `get_logs(service_id?)` → `Vec<LogEntry>` (reads from ProcessManager's ring
  buffer, optionally filtered by service ID)

`all_healthy` treats services with no port as passing: only services with a
defined port contribute to the aggregate. This prevents mcp/poller (no health
URL) from making the system always look unhealthy.

Used by: System page health polling and LogViewer data fetch.

## Gotchas

`get_health_status` is called on demand (each time the frontend polls). There
is no background health loop on the Rust side. The debounce in
`HealthMonitor` accumulates across calls — calling `get_health_status`
repeatedly within seconds will debounce transient failures before reporting
`Unhealthy`.
