---
code_file: tauri/src-tauri/src/sidecar/health_monitor.rs
last_verified: 2026-04-10
---

# health_monitor.rs — TCP port reachability check with debounce

`check_service(service_id, port)` opens a TCP connection to `127.0.0.1:port`.
Success → `Healthy`. Failure → increments a per-service failure counter; only
returns `Unhealthy` after `debounce_threshold` (2) consecutive failures to
avoid transient startup blips showing as unhealthy.

Services with no port (mcp, poller, job_trigger, message_bus_trigger) return
`Unknown` — they are checked by the logic in `commands/health.rs` which
treats `Unknown` as passing for the `all_healthy` aggregate.

## Why TCP instead of HTTP health endpoints

Most services (mcp, poller) don't have an HTTP health endpoint. TCP connect
is the lowest common denominator. The backend does have a `/docs` health URL
in its `ServiceDef` but `health_monitor.rs` doesn't use it — it just checks
port 8000.

## Upstream / downstream

- **Used by:** `commands/health.rs` (`get_health_status` IPC command), called
  once per `get_health_status` invocation (no background loop — polling is
  driven by the frontend's polling interval)
- **Holds state:** `unhealthy_counts` map for debounce (resets on success)
