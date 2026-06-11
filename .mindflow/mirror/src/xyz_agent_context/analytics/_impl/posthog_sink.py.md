---
code_file: src/xyz_agent_context/analytics/_impl/posthog_sink.py
last_verified: 2026-06-10
stub: false
---

# posthog_sink.py

## 2026-06-10 — review fix: dead `disabled = False` line removed

The constructor used to set `self._ph.disabled = False` with a comment about
the free tier — a no-op (`disabled` already defaults to False) whose comment
described nothing the line did. Removed; event volume is controlled by only
instrumenting the five funnel events, not by any client flag.

## Why it exists

The only file in the codebase that knows the PostHog Python SDK's shape. All
other modules depend on the `AnalyticsClient` Protocol; this is the concrete
adapter that bridges the Protocol to the `posthog` package. Isolating it here
means swapping PostHog for another vendor touches exactly this one file.

## Upstream / downstream

- **Instantiated by**: `analytics/__init__._build_sink()` — only when
  `NARRA_ANALYTICS_ENABLED=true`, `POSTHOG_API_KEY` is set, and
  `SURFACE != "cloud"`. The import of `posthog` itself is deferred to the
  `__init__` method so the module loads cleanly in environments where the
  package is absent.
- **Depends on**: `posthog>=7.18.0` (a main dependency in `pyproject.toml`,
  not an optional extra). Accesses
  the SDK via an instance client (`posthog.Posthog(project_api_key=...,
  host=...)`), not the deprecated module-level global API.

## Design decisions

**Instance client, not module-level globals**: `posthog.Posthog(...)` creates
an isolated client object. This avoids the global state problems of the older
`posthog.capture(...)` module-level API, and makes it straightforward to have
multiple sinks (e.g. dev + prod keys) in a test harness without them
interfering.

**`identify()` maps to the client's `set()`**: posthog-python 7.x removed
`Client.identify()`; person traits are now set via `Posthog.set(distinct_id=,
properties=)` ("set properties on a person profile"). The sink's `identify`
method is the single place that absorbs this SDK quirk, so callers keep using
the vendor-neutral `identify` name from `base.AnalyticsClient`.

**Best-effort: all three methods swallow exceptions**: any error in `capture`,
`identify`, or `flush` is logged at WARNING level and then discarded. The
analytics observer must never interrupt the observed code path. This mirrors the
"observer never breaks observed" contract stated in `base.py`.

**PostHog batches on a background thread**: `Posthog.capture()` returns
immediately and enqueues the event for a background thread that periodically
POSTs to the PostHog ingest endpoint. `flush()` blocks until the queue drains.
This is why `shutdown_analytics()` in `analytics/__init__.py` must call
`flush()` — without it, events buffered since the last automatic flush would be
lost on process exit.

**`host` defaults to `"https://us.i.posthog.com"`**: this is the PostHog US
region ingest endpoint. EU customers or self-hosted deployments override via the
`POSTHOG_HOST` env var, which `analytics/__init__._build_sink()` passes through
to the constructor.

## Gotchas

- The lazy `import posthog` inside `__init__` means a missing/broken package
  raises at `PostHogSink()` construction time, not at module import time.
  `_build_sink()` itself has no try/except — the construction error would
  propagate out of `get_analytics()` and be swallowed by the `try` in
  `track()` / `identify_user()` (logged at WARNING). Net effect: analytics
  silently off, app unharmed.
- Do not call `self._ph.shutdown()` instead of `self._ph.flush()`. `shutdown()`
  permanently disables the client; subsequent events (if any) would be dropped
  silently. `flush()` drains without disabling.
