---
code_file: tests/marketplace/test_skill_seed.py
last_verified: 2026-07-22
stub: false
---

# test_skill_seed.py

Seeds a fixture marketplace_skills/ tree (one default + one normal): asserts
both publish, is_default preserved, blob in store, list_defaults returns only
the default; idempotency (second pass re-publishes nothing, same hash);
no-op when the dir is missing; and a check against the REAL repo that
netmind-vision/netmind-transcribe exist with default=true.
