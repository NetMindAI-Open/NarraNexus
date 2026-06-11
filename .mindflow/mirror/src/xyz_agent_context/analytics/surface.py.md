---
code_file: src/xyz_agent_context/analytics/surface.py
last_verified: 2026-06-08
stub: false
---

# surface.py

## Why it exists

Determines which surface (launcher context) the current backend process is
serving — `local`, `desktop`, or `cloud`. This identity is stamped on every
analytics event as `PROP_SURFACE` so the PostHog dashboard can segment funnel
metrics by how users run NarraNexus.

The value is resolved once at module import time and exposed as the
module-level constant `SURFACE`. All callers import this constant; no one calls
`resolve_surface()` directly at runtime.

## Upstream / downstream

- **Consumed by**:
  - `analytics/__init__.py` — reads `SURFACE` to gate the PostHog sink (cloud
    → NullSink this phase) and to stamp `surface` onto every event
  - `analytics/events.py` — defines `PROP_SURFACE` which capture sites pair
    with this value
- **Depends on**: `os.environ` only — no DB, no network, no other module.

## Design decisions

**Env var, not HTTP header or DB flag**: surface identity is a process-level
property set by the launcher, not a per-request or per-user property. Using
`NARRA_SURFACE` as an environment variable means:

- `dev-local.sh` sets `NARRA_SURFACE=local` for the dev loop
- The Tauri sidecar launch script sets `NARRA_SURFACE=desktop` before
  spawning the backend
- Cloud container entrypoints set `NARRA_SURFACE=cloud`

This is unforgeable (the process environment is set by the launcher before any
request arrives) and never accidentally dropped (unlike a header that a proxy
or client might omit).

**Default to `"local"`**: an unset or invalid `NARRA_SURFACE` resolves to
`"local"`. This is the safest default for development — local runs are far more
common than the other surfaces and misconfiguration is immediately visible in
event properties rather than silently mis-categorising traffic.

**Resolved once, not per-call**: reading `os.environ` on every `track()` call
would be harmless performance-wise, but resolving at import time makes the
value immutable after process start, which simplifies testing (patch the module
attribute rather than the env var) and documents that surface is a
process-lifetime property, not a per-request one.

## Gotchas

- In tests, patch `xyz_agent_context.analytics.surface.SURFACE` (the constant)
  not `os.environ["NARRA_SURFACE"]`. By the time the test runs, the module is
  already imported and `SURFACE` is already frozen.
- If `NARRA_SURFACE` is set to any value not in `{"local", "desktop", "cloud"}`
  the fallback is `"local"`. This is intentional — typos in the env var should
  not produce an unknown surface value in the analytics dashboard.
