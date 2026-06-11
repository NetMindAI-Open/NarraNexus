---
code_file: src/xyz_agent_context/analytics/_impl/fake_sink.py
last_verified: 2026-06-08
stub: false
---

# fake_sink.py

## Why it exists

An in-memory `AnalyticsClient` implementation used exclusively in tests. It
records every `capture` and `identify` call so test assertions can verify:

- which funnel events fired
- what properties each event carried
- how many times `flush()` was called (useful for shutdown tests)

Without `FakeSink`, testing analytics instrumentation would require either
mocking at the module boundary (fragile) or letting events silently disappear
into `NullSink` with no way to assert they fired.

## Upstream / downstream

- **Used by**: test files that call `analytics.track()` or
  `analytics.identify_user()` and need to assert the events.
- **Not used in production**: `_build_sink()` in `analytics/__init__.py` never
  produces a `FakeSink`; tests inject it by patching
  `analytics._get_sink_cached` or by constructing a `FakeSink` directly and
  passing it to the unit under test.
- **No dependencies**: pure Python, no external packages.

## Design decisions

**Three recording lists**: `events`, `identities`, and a `flushed` counter.
This minimal state is sufficient for asserting all current funnel scenarios.
Adding new assertion needs (e.g. call ordering across event types) can be done
by storing a single chronological `calls` list in a future iteration — not done
now because it isn't needed and would complicate the simple assertions.

**Stores raw arguments, not copies**: `events` tuples contain the exact
`(distinct_id, event, properties)` values passed by the caller. Tests compare
against these directly. If tests mutate the properties dict after passing it to
`capture`, they would see the mutation reflected here — this is unlikely but
worth knowing.

**`flushed` is a counter, not a bool**: counts the number of `flush()` calls
rather than recording a boolean. This allows tests to assert that flush was
called exactly once during shutdown (not zero, not twice).

## Gotchas

- When patching `analytics._get_sink_cached` to return a `FakeSink`, remember
  to also clear `lru_cache` state between tests (`_get_sink_cached.cache_clear()`)
  if the cache was already populated by a prior test in the same session.
- `FakeSink` does NOT invoke the opt-out check. The opt-out guard lives in
  `track()` / `identify_user()` in `analytics/__init__.py`. If testing opt-out
  behaviour, call those public functions (not the sink directly) and patch the
  DB lookup.
