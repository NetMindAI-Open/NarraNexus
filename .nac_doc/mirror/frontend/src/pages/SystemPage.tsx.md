---
code_file: frontend/src/pages/SystemPage.tsx
last_verified: 2026-04-10
stub: false
---

# SystemPage.tsx — Service health monitor and log viewer

## Why it exists

In local mode, the backend runs as multiple processes (FastAPI, MCP server, ModulePoller). The user needs visibility into whether these services are running, their health status, and their log output. `SystemPage` provides that dashboard. It is only accessible when `runtimeStore.features.showSystemPage === true` (local mode only).

## Upstream / Downstream

Route: `/app/system`, rendered inside `MainLayout`. Uses `lib/platform.ts` exclusively for data — calls `platform.getServiceStatus()`, `platform.getLogs()`, `platform.onHealthUpdate()`, `platform.onLog()`, and the start/stop/restart methods.

Composes `components/system/ServiceCard`, `HealthStatusBar`, and `LogViewer`. No store reads.

In the current codebase, `TauriBridge` throws on all methods, so `SystemPage` renders the "Platform Not Available" placeholder for every non-Tauri deployment. This is the expected state until Phase 4 Tauri integration is built.

## Design decisions

**Defensive try/catch around every `platform.*` call.** Since `TauriBridge` throws, every call to the platform bridge is wrapped. Service actions (`handleStartAll`, `handleStopAll`, `handleRestart`) silently swallow errors. The status fetch shows a placeholder if it fails and `processes.length === 0`.

**`resolveStatus` merges `ProcessInfo` and `ServiceHealth`.** Two data sources exist: `getServiceStatus()` returns process-level info (PID, start time, `status` string), and `onHealthUpdate` pushes per-service health state (healthy/unhealthy with port). The function merges them preferring `ServiceHealth` when available.

**3-second polling for process status.** `setInterval(fetchStatus, 3000)` provides fresh data without relying solely on push events. Both polling and the `onHealthUpdate` subscription run simultaneously — the push updates may arrive faster, but the poll ensures consistency.

**Log buffer capped at 500 entries.** `setLogs((prev) => [...prev.slice(-499), entry])` keeps the last 500 log lines. Without this cap, memory usage would grow unbounded in long-running sessions.

**Ref-based unsubscribe.** `unsubHealthRef` and `unsubLogRef` store the unsubscribe functions returned by `platform.onHealthUpdate()` and `platform.onLog()`. The `useEffect` cleanup calls them if they are set. If the subscription method throws (Tauri not available), the refs remain null and cleanup is a no-op.

## Gotchas

**`showSystemPage` feature flag controls sidebar visibility, not routing.** The route `/app/system` exists regardless of mode. If a user navigates directly to `/app/system` in cloud mode (where `showSystemPage === false`), they see the platform error placeholder rather than a 404 or redirect. This is acceptable for now but could be improved with a route-level guard.

**Tauri Phase 4 TODO.** All `platform.*` calls in this page are currently no-ops (throw → catch → placeholder). When Tauri integration is implemented, `TauriBridge` methods must be filled in without changing this component.
