---
code_file: frontend/src/types/platform.ts
last_verified: 2026-07-22
stub: false
---

## Why it exists

The TypeScript contract types for the platform-abstraction layer — the shapes
exchanged between the frontend and the desktop runtime (Tauri) / cloud backend.
Pure type declarations, no logic. Consumed by [[platform.ts]] (the bridge),
`SystemPage`, and `api.ts`.

## What's here

- `AppMode` (`local` | `cloud-web`) + `UserType` — the two deploy surfaces.
- `ProcessInfo` — one sidecar process's status as ProcessManager reports it
  (serde camelCase from Rust): `serviceId / label / status / pid / restartCount
  / lastError`.
- `ServiceHealth` / `OverallHealth` — the Tauri `get_health_status` payload;
  `allHealthy` only weighs port-bound services (portless → don't drag it down).
- `LogEntry` — a drained sidecar log line.
- **`WorkerState` / `WorkerLiveness` / `WorkerStatus`** (added 2026-07-22) — the
  per-worker liveness of the consolidated `workers` supervisor
  ([[run_worker_supervisor.py]]), sourced from `GET /api/admin/runtime/workers`
  ([[admin_runtime.py]]). `restartCount` is cumulative (a climbing count is the
  "flapping" signal); `heartbeatAgeSeconds` lets the UI detect a stale snapshot
  (dead supervisor → frozen row still returns available:true). Rendered by
  [[ServiceCard.tsx]].
- `AppConfig` / `FeatureFlags` — mode + capability gating for the shell.

## Gotchas

These are cross-boundary contracts: `ProcessInfo` / `ServiceHealth` mirror Rust
structs (serde `rename_all = "camelCase"`), and `WorkerStatus` mirrors the
FastAPI JSON (snake_case → camelCase mapping happens in `api.getWorkerStatus`,
not here). Renaming a field here without the matching Rust/Python change
silently yields `undefined` at runtime.
