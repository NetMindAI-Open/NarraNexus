---
code_file: src/narranexus/platform/repository/product_analytics_repository.py
last_verified: 2026-08-19
stub: false
---

# product_analytics_repository.py — atomic first-party fact persistence

This repository owns the database shape for product facts so the public
`analytics.track()` seam stays limited to opt-out, surface stamping, and
failure isolation. Query-critical dimensions are copied into columns and the
controlled property map is retained as compact JSON; capture sites must never
pass conversation content, credentials, email, or free-form PII.

Stable event IDs use insert-first atomic deduplication. The database unique key
chooses the first writer, and duplicate-key errors are treated as successful
replays without a SELECT race or warning noise. Other database errors
propagate to `track()`, which logs and swallows them so observation cannot
break the observed product flow.

Unique-violation detection routes through the shared [[dialect_errors.py]]
`is_unique_violation` (dual-dialect; case-**insensitive** after predicate-ising,
one of six sites converged in PR#327). A conflict → silent `return` (the first
fact wins); every other error still propagates up to `track()`.
