---
code_file: backend/migrations/m0004_channel_credentials_backfill.py
last_verified: 2026-09-04
stub: false
---

# m0004 — channel_credentials backfill

## Intent

One-shot, idempotent copy of the bespoke channel bindings into `channel_credentials` (first run in the dual-write phase of batch 4b; repeated by m0005 at the 4d switch).

## 2026-09-04 · body moved to `credential_legacy` (batch 4d.2)

The copy is now `channel/credential_legacy.copy_legacy_tables` (the same legacy-table description the bundle importer uses); this migration is the 4b-era invocation, [[m0005_channel_credentials_switch]] the 4d one. Intent unchanged: idempotent, copy not move, legacy tables never dropped.
