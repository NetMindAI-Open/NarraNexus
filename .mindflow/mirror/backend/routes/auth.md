---
code_file: backend/routes/auth.py
last_verified: 2026-05-21
stub: false
---

# auth.py — auth + user + agent-listing REST routes

## Why it exists

The control-layer surface for everything an unauthenticated-to-just-logged-in
client needs before it has an agent context: login / user creation, the
per-user agent list, and agent CRUD. It sits above the repositories and
does request-shaped enrichment that no single repository owns (sidebar
previews, active-run status, bootstrap-pending detection).

## Decisions

- **`GET /api/auth/agents` enriches in bulk, not N+1.** Active runs and the
  last-assistant sidebar preview are each pulled in a single SELECT keyed
  by the page's agent_ids (window function for the preview, `IN (...)` for
  active runs). A user with many agents must not fan out into per-agent
  queries.
- **active_run is derived, not stored on the agent.** It comes from the
  `events` table (`state='running'`). The agent row itself has no run
  state — runs are events.
- **Heartbeat-liveness filter on active_run (`_run_is_live`).** A run whose
  task died without `_finalize` (process killed mid-run, or the terminal DB
  write failed) leaves its events row stuck at `state='running'`. The
  startup reconcile in `backend/main.py` only flips such rows on the *next*
  restart, so between restarts the sidebar avatar would pulse "running"
  forever for an agent that is not running. We therefore only surface a
  `running` row as active_run while its heartbeat (`last_event_at`, falling
  back to `started_at`) is within `_RUN_STALE_AFTER_S` (3 × the
  `BackgroundRun` heartbeat cadence). This is **read-side only** — it never
  stops or mutates a run, so a genuinely long agent_loop keeps beating and
  stays live (CLAUDE.md 铁律 #14). Fails open on missing/unparseable
  timestamps so we never hide a possibly-live run.
- **Enrichment failures degrade, never 500.** Both the active-run and the
  preview enrichment are wrapped: if either query breaks, the listing still
  returns with that field omitted rather than failing the whole page.

## Gotchas

- `last_event_at` is stored UTC but comes back as an ISO string from SQLite
  and as a `datetime` from MySQL — `_parse_db_utc` normalises both (and
  naive → UTC) before comparison. Don't assume one type.
- The startup reconcile (`backend/main.py`) and this runtime filter are two
  halves of the same invariant ("a dead run must not look running"); they
  use the same notion of staleness from opposite ends (restart sweep vs.
  live read). Change one, reconsider the other.
