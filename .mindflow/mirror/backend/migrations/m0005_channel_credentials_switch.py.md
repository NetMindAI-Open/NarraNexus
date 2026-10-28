---
code_file: backend/migrations/m0005_channel_credentials_switch.py
last_verified: 2026-09-04
stub: false
---

# m0005 — channel_credentials switch

## Intent

Batch 4d moved every builtin channel's reads onto `channel_credentials`. `m0004` copied the bespoke rows in during the dual-write phase, but any binding written to a bespoke table AFTER m0004 ran (an install that deployed 4b, kept binding bots, then deployed 4d) would be invisible to the switched managers. This migration re-runs the same idempotent copy (`credential_legacy.copy_legacy_tables`) once more at the switch, so no binding is lost between the two deploys. Running it on a fresh install or a fully copied one is a no-op. The legacy tables stay (rule #6).
