---
code_file: src/xyz_agent_context/analytics/__init__.py
last_verified: 2026-06-08
stub: false
---

# __init__.py (analytics)

## Why it exists

This is the single public surface of the analytics subsystem. Every capture
site in the codebase imports only from this module — `track()`,
`identify_user()`, `shutdown_analytics()`, and `get_analytics()`. Nothing
outside `analytics/` ever touches a sink directly or reads `SURFACE` itself.

The design goal is that adding or swapping a vendor, changing the gating
logic, or toggling the opt-out mechanism requires touching exactly one file.

## Upstream / downstream

- **Consumed by**: any route handler, service, or lifecycle hook that fires a
  funnel event (e.g. `backend/routes/auth.py` for `signed_up`,
  `backend/main.py` lifespan for `shutdown_analytics`).
- **Depends on**:
  - `analytics/base.py` — `AnalyticsClient` Protocol (re-exported here)
  - `analytics/surface.py` — process-level `SURFACE` constant
  - `analytics/_impl/null_sink.py` — default / disabled path
  - `analytics/_impl/posthog_sink.py` — lazy-imported only when enabled
  - `repository/user_settings_repository.py` — opt-out DB lookup
  - `utils.get_db_client` — DB connection for opt-out check

## Design decisions

**Hashed distinct_id (`_hash_distinct_id`)**: `track`/`identify_user` never
send the raw `user_id` to the sink — they send `sha256(salt:user_id)[:32]`.
The local `user_id` is often a human-chosen name, so this keeps real names
out of the PostHog dashboard (pseudonymization — the salt is in source, so
it is reversible by a determined attacker with a guess-list, but that is the
accepted product-analytics tradeoff). Critically, the `_opted_out` lookup
uses the RAW `user_id` because it only queries the local DB; nothing
identifying leaves the machine there. Downstream contract: anything that
needs to NOT leak identity (e.g. identify traits) must avoid raw names too.

**Three-gate gating in `_build_sink()`**: the sink is NullSink unless ALL of
the following pass: `NARRA_ANALYTICS_ENABLED=true`, `POSTHOG_API_KEY` is set,
and `SURFACE != "cloud"` (cloud is deferred this phase). Any gate failure
silently falls back to NullSink. The check order is important — the cloud gate
is intentionally second so it short-circuits before attempting a key lookup
when the key is irrelevant.

**`lru_cache(maxsize=1)` on `_get_sink_cached()`**: the sink is constructed
once per process lifetime. All three call sites (`track`, `identify_user`,
`shutdown_analytics`) share the same instance, which is required for PostHog's
background-thread batcher to accumulate events across calls and flush them
together on shutdown.

**`PostHogSink` is lazily imported**: `posthog` is an optional dependency. The
`import posthog` at the top of `_build_sink` would raise if the package is
absent. Deferring it to the enabled path ensures the module loads fine in
test / CI environments where `posthog` is not installed.

**`_opted_out()` is best-effort**: if the DB lookup fails (e.g. during tests
where no DB is wired) it logs a warning and defaults to `False` (tracking on).
This is the safer failure mode — a broken opt-out lookup should not silently
suppress funnel data.

**`track()` and `identify_user()` are `async`**: solely because `_opted_out`
hits the DB via `await`. The sink methods themselves (`capture`, `identify`)
remain sync; the async boundary lives here in the public API, not in the
protocol.

**`surface` auto-stamped on every event**: `track()` calls
`props.setdefault("surface", SURFACE)` so capture sites never need to pass it
explicitly — but they can override it if needed.

## Gotchas

- Do not call `get_analytics()` at module import time (e.g. as a module-level
  variable). The `_build_sink()` path reads environment variables; reading them
  before the process launcher sets them (e.g. in `dev-local.sh`) gives a stale
  result that is then frozen by `lru_cache`.
- `shutdown_analytics()` must be awaited in the lifespan shutdown path
  (after `close_db_client`), not fire-and-forget. Skipping it silently drops
  all buffered PostHog events that haven't been flushed by the background thread.
