---
code_file: tests/marketplace/test_reconciler.py
last_verified: 2026-07-21
stub: false
---

# test_reconciler.py

SkillSyncService branch coverage on a real tmp filesystem: manual dir →
row added (provenance taken from a travelling .skill_meta.json when
present); deleted dir → external_removed; hand-edit → modified (via
content_hash); disable/enable round-trip; reconcile_all walks the nested
{user}/{agent} layout and is idempotent; and the invariant test — a
reconcile pass never creates/modifies/deletes any user file.
