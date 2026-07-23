---
code_file: frontend/src/components/system/ServiceCard.tsx
last_verified: 2026-07-22
---

## 2026-07-22 (PR #136 review) — stale-snapshot guard + flap semantics

Two follow-ups: (1) the header flap-warning now fires ONLY when a sub-worker is
`state === 'restarting'` (currently down), not `restartCount > 0` — a long-ago
restart leaves the cumulative count on the per-row badge but no longer pins the
⚠ on forever. (2) New `workerHeartbeatAgeSeconds` prop: when the snapshot is
older than `STALE_AFTER_S` (90 s = 3× the 30 s heartbeat), the card shows a
muted "stale · Xm ago" marker, grays every worker dot, and suppresses the flap
warning. Without this a persistent `service_audit` heartbeat row from a
long-dead supervisor still returned `available:true` and rendered all workers
green — swapping the card's original failure mode for "stale snapshot hides a
dead supervisor". Age comes from [[SystemPage.tsx]] via `WorkerStatus`.

## 2026-07-22 — optional expandable per-worker detail

Gained an optional `workers?: WorkerLiveness[]` prop. When present (only the
consolidated `workers` service passes it — see [[SystemPage.tsx]] /
[[run_worker_supervisor.py]]), the card renders: (1) a **flap warning** icon in
the header when any sub-worker is `restarting` or has `restartCount>0` — the
whole point, since the process-level dot reads "running" even while a sub-worker
crash-loops; (2) an expandable list of each sub-worker's state dot + name +
restart-count badge. Data comes from `api.getWorkerStatus()` →
`GET /api/admin/runtime/workers`. Card is otherwise unchanged for every other
(port-bound or plain) service.

# ServiceCard.tsx — Status card for one backend service

Shows an animated status dot (pings for healthy/running/starting, static for
crashed/stopped), service label, port number, last error, and an optional
restart button.

Pure display component. All state lives in the System page parent. The restart
button calls `onRestart` which in Tauri mode maps to the `restart_service`
IPC command.

Used by: System page (one card per entry in `OverallHealth.services`).
