---
code_dir: frontend/src/components/system/
last_verified: 2026-04-10
---

# system/ — Service health dashboard and real-time log viewer

The System page shows whether all backend processes are running and lets
users see their stdout/stderr output. In the Tauri desktop app these are the
Python sidecar processes; in the web-only mode they are the externally-run
services.

## Component tree

```
(SystemPage — in pages/ or routes/)
  ├── HealthStatusBar          ← top banner: all healthy / N unhealthy / unavailable
  ├── ServiceCard (×n)         ← one card per service with status dot + restart button
  └── LogViewer                ← scrollable terminal with per-service filter tabs
```

## Data sources

- **Health:** Tauri command `get_health_status` (Tauri mode) or REST endpoint
  (`/api/health`). Returns `OverallHealth` with per-service TCP reachability.
- **Logs:** Tauri command `get_logs` — reads from the ring buffer maintained
  by `process_manager.rs`'s `spawn_log_drainer`. In web-only mode, logs are
  not available.

## Why these components are small

They are pure display components. All data fetching and polling lives in the
page/route that renders them. The components only need to know how to render
what they receive.
