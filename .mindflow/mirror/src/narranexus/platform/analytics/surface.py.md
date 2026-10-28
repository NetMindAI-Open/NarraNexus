---
code_file: src/narranexus/platform/analytics/surface.py
last_verified: 2026-06-08
stub: false
---

# surface.py

## Why it exists

Determines which surface (launcher context) the current backend process is
serving — `local`, `desktop`, or `cloud`. This identity is stamped on every
first-party event as `PROP_SURFACE` so database queries can segment usage by
how users run NarraNexus.

The value is resolved once at module import time and exposed as the
module-level constant `SURFACE`. All callers import this constant; no one calls
`resolve_surface()` directly at runtime.

## Upstream / downstream

- **Consumed by**:
  - `analytics/__init__.py` — stamps `surface` onto every persisted event
  - `analytics/events.py` — defines `PROP_SURFACE` which capture sites pair
    with this value
- **Depends on**: `os.environ` and the pure-environment
  `utils.deployment_mode` resolver — no DB or network.

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

**Fallback to canonical deployment mode with a warning**: an unset or invalid
`NARRA_SURFACE` resolves through `NARRANEXUS_DEPLOYMENT_MODE` and its existing
database-URL heuristic. This keeps direct-uvicorn cloud stacks correctly
labelled even when they bypass `run.sh`; the warning makes a missing launcher
contract observable. Desktop remains explicit because deployment mode only
distinguishes cloud from local.

**Resolved once, not per-call**: reading `os.environ` on every `track()` call
would be harmless performance-wise, but resolving at import time makes the
value immutable after process start, which simplifies testing (patch the module
attribute rather than the env var) and documents that surface is a
process-lifetime property, not a per-request one.

## Gotchas

- In tests, patch `narranexus.platform.analytics.surface.SURFACE` (the constant)
  not `os.environ["NARRA_SURFACE"]`. By the time the test runs, the module is
  already imported and `SURFACE` is already frozen.
- If `NARRA_SURFACE` is invalid, it never becomes an unknown database value;
  the process logs the bad value and uses canonical cloud/local inference.
