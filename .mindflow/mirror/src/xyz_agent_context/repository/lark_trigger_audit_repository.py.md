---
code_file: src/xyz_agent_context/repository/lark_trigger_audit_repository.py
stub: false
last_verified: 2026-04-21
---

## Why it exists

The Lark trigger runs in its own container and production users often
cannot pull logs off EC2. When the bot misbehaves (silent gaps, burst
of old replies, stuck workers), post-incident reviewers have had
nothing durable to look at. This repository is the trigger's black-box
recorder: every lifecycle event lands in `lark_trigger_audit` with a
JSON `details` blob.

## Design decisions

- **Append-only** — no updates, no deletes other than retention cleanup.
  Queries are filter + limit; no aggregation in the DB.
- **`append` never raises** — audit writes are best-effort. Losing an
  audit row is preferable to breaking real user traffic. Failures fall
  through to loguru.
- **30-day retention** — longer than `lark_seen_messages`' 7-day window.
  Incident review windows are usually weeks.
- **JSON `details` column** — arbitrary debug context can be stashed
  without migrations. Downstream consumers only read fields they
  expect; unknown keys are inert.
- **String-ised timestamps in sort/compare** — sqlite returns
  `datetime` objects, mysql returns strings; the `_event_time_str`
  helper normalises both sides.

## Upstream / downstream

- **Upstream**: `LarkTrigger` calls `append(...)` at every lifecycle
  node (ingress decision, WS connect/disconnect, worker error/timeout,
  heartbeat, subscriber start/stop, inbox-write fallback).
- **Downstream**: `/healthz` endpoint consumes `count_by_type`; a
  future admin UI can read `recent(...)` directly.

## Gotchas

- `count_by_type` fetches all rows then counts in Python. Fine at the
  expected volume (N hours × sparse events), but if audit writes ever
  become firehose-y, add a server-side aggregate.
- `cleanup_older_than_days` uses the dialect-agnostic `db.delete`
  per-id rather than a bulk DELETE to sidestep the sqlite/mysql
  placeholder mismatch that bit the `lark_seen_messages` cleanup
  path (see `lark_seen_message_repository.py`).
- Event-type constants are module-level strings, not an Enum, so
  callers can grep for string literals and the DB column stays a
  simple VARCHAR.
