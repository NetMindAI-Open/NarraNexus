---
code_file: tests/backend/test_notices_routes.py
last_verified: 2026-07-03
---

# test_notices_routes.py — user-notices read side (upstream #52)

Locks: own-rows-only listing + unread count, unread_only filter, mark-read,
and the 404-masks-foreign-rows tenancy policy (mirrors bus-failures).
