---
code_file: src/xyz_agent_context/analytics/_impl/__init__.py
last_verified: 2026-06-10
stub: false
---

# analytics/_impl/__init__.py

## Why it exists

Empty package marker for the private implementation layer of the analytics
package, following the repo-wide `_*_impl/` convention: concrete sinks
(`posthog_sink.py`, `null_sink.py`, `fake_sink.py`) live here and are
**never re-exported**. Callers import only from `xyz_agent_context.analytics`
(the public `track()` / `identify_user()` / `get_analytics()` API) or, for
tests, reach into `_impl.fake_sink` explicitly.

## Design decisions

Intentionally empty — re-exporting the sinks here would invite callers to
construct them directly and bypass the env/surface gating in
`analytics/__init__._build_sink()`.
