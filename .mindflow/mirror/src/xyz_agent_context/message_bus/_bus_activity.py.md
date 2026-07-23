---
code_file: src/xyz_agent_context/message_bus/_bus_activity.py
last_verified: 2026-07-22
stub: false
---

# _bus_activity.py — live "what is this agent doing" for team rooms

## Why it exists

A team-room agent runs in the background via [[message_bus_trigger]] — the team chat UI has
no WebSocket stream to it (unlike the single-agent path, which gets `events`/`event_stream`/
Broadcaster telemetry from `BackgroundRun`). This module is a **cheap status mirror**: the
trigger writes running/phase/heartbeat into `bus_agent_activity` around + during a run, and
`backend/routes/teams.py::get_team_chat` reads it to show running / phase / elapsed.

Deliberately NOT the `events` pipeline (which is WS-only and heavier). One row per
(agent_id, channel_id); `state` flips `running`→`idle` at turn end.

## Shape

`mark_running` (start) → `update_phase` (thinking / tool:<name> / replying, throttled by the
trigger's `_make_activity_progress`) → `mark_idle` (end, in a `finally`). `is_live(row)` is
the reader-side guard: a `running` row whose `updated_at` heartbeat is older than
`ACTIVITY_STALE_SECONDS` (90s) reads as not-live (the trigger process died mid-run).

## Gotchas

- Writes go through the dialect-safe `AsyncDatabaseClient` (`get_db_client()`), not the raw
  bus backend — `_upsert` is update-or-insert on the composite PK (agent_id, channel_id).
- Progress is fed by the opt-in `on_progress` callback on `run_collector.collect_run` (only
  the team branch passes one; every other trigger passes None → zero overhead).
- Status writes must never break delivery — the trigger swallows their errors.
