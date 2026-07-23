---
code_file: tests/marketplace/test_repositories.py
last_verified: 2026-07-20
stub: false
---

# test_repositories.py

Repository tests for the three marketplace tables, on the shared in-memory
`db_client` fixture (tables come from schema_registry via auto_migrate, so
these tests also prove the registrations are valid SQLite DDL).

Deliberate coverage choices: semver ordering uses 1.9.0 vs 1.10.0 (breaks
string comparison); search-dedup proves "one card per skill_id, latest
version"; installation uniqueness is asserted on the full
(agent_id, user_id, skill_id) triple; scan results assert append-only +
latest-wins semantics.
