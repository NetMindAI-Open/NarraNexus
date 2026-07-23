---
code_file: src/xyz_agent_context/repository/skill_scan_result_repository.py
last_verified: 2026-07-20
stub: false
---

# skill_scan_result_repository.py

Append-only store of security-scan runs over published skill versions.
Written by the publish pipeline (and future re-scans when scanner rules
upgrade); read by the detail API and the publish gate.

## Design decisions

- **Append, never update**: the same (skill_id, version) may be scanned many
  times; `latest_for()` picks the newest row by `ORDER BY id DESC` (id is
  monotonic and unambiguous, unlike second-resolution timestamps).
- The non-unique `(skill_id, version)` index in schema_registry exists for
  exactly this query.
