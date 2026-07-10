---
code_file: src/xyz_agent_context/channel/channel_health_server.py
stub: false
last_verified: 2026-07-08
---

## Why it exists

One `/healthz` endpoint reporting a per-channel snapshot for EVERY consolidated
channel trigger. Generalised from the old `lark_module/_health_server.py`
(deleted 2026-07-08), which only snapshotted a single `LarkTrigger`. Now that
every channel runs inside one supervisor process, a single aggregated health
server also closes the old observability gap where only Lark had an endpoint.

## Design decisions

- **Reads only base-class attributes.** `_snapshot_one` reads `running`,
  `_startup_time_ms`, `_subscriber_tasks`, `_workers`, `_task_queue`,
  `_subscriber_creds`, `_audit_repo` — all on `ChannelTriggerBase`, so it works
  for any channel. Lark-specific `_last_ws_connected_wallclock_ms` is read via
  `getattr(..., 0)`, so channels that don't track it report 0 instead of
  crashing.
- **Overall status = ok only if every channel is ok.** Any channel still
  `starting` (no audit repo yet / not running) makes the aggregate `degraded`.
- **Best-effort, never blocks startup.** If fastapi/uvicorn aren't installed
  (tests, stripped image) `start_channel_health_server` returns None and the
  supervisor runs without health. `count_by_type` failures degrade to empty
  counts, never raise.
- **Port 47831 unchanged** from the Lark server (quiet range, no collision with
  the 74xx fleet; container-internal, not published).

## Upstream / downstream

- **Upstream**: `run_channel_triggers` calls `start_channel_health_server(started)`.
- **Downstream**: each trigger's `_audit_repo.count_by_type` (L3 event counts).

## Gotchas

- Started ONLY by the supervisor now. `LarkTrigger.start()` used to spawn its own
  health server; that was removed to avoid double-binding port 47831.
