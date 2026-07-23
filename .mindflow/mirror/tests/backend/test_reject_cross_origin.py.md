---
code_file: tests/backend/test_reject_cross_origin.py
last_verified: 2026-07-22
stub: false
---

# test_reject_cross_origin.py

Unit tests for `backend/auth.py::reject_cross_origin` — the CSRF guard for
tokenless local-mode writes (marketplace skills/teams publish). Locks in the
decision table so a future refactor can't silently reopen the holes:

- no `Origin` header (CLI / same-origin) → allowed
- loopback `Origin` (localhost, 127.0.0.1) → allowed
- foreign-site `Origin` → 403
- `Origin: null` (sandboxed iframe / `data:` form) → 403 — the specific
  bypass the second review flagged; must NOT be treated as same-origin
- `Sec-Fetch-Site: cross-site` → 403; `same-origin`/`none` → allowed

Pure-function tests with a tiny header stub — no app/client needed.
