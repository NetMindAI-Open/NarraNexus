---
code_file: src/xyz_agent_context/analytics/_impl/null_sink.py
last_verified: 2026-06-08
stub: false
---

# null_sink.py

## Why it exists

Provides a completely inert `AnalyticsClient` implementation used in three
situations:

1. Analytics is explicitly disabled (`NARRA_ANALYTICS_ENABLED != "true"`)
2. `POSTHOG_API_KEY` is not set
3. `SURFACE == "cloud"` (cloud analytics deferred this phase)

Having a real no-op object that satisfies the `AnalyticsClient` Protocol is far
preferable to sprinkling `if analytics_enabled:` guards everywhere at the call
site. The gating decision is made once in `_build_sink()`; after that, all
call sites are identical regardless of whether analytics is on or off.

## Upstream / downstream

- **Instantiated by**: `analytics/__init__._build_sink()` — as the default
  when any gate condition fails.
- **Used as test default**: tests that do not need to assert on analytics calls
  can let `get_analytics()` return a NullSink (by not setting the env vars)
  and pay zero overhead.
- **No dependencies**: no imports beyond `__future__` and `typing`. Must stay
  dependency-free so it can be imported in any environment, including
  lightweight CI containers without optional packages.

## Design decisions

**`return None` explicitly**: each method returns `None` rather than `pass`.
This is a style choice that makes the no-op intent visually explicit when
reading the file quickly, and satisfies type checkers that expect `-> None`.

**No logging**: unlike `PostHogSink`, which logs failures, `NullSink` is
silent by design. Logging "event dropped (analytics disabled)" on every
`track()` call would be noisy and uninformative. The fact that it's a NullSink
is already visible at startup through the absence of a PostHog init log line.
