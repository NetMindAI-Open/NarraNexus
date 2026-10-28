---
code_file: tests/conftest.py
last_verified: 2026-10-28
stub: false
---

# tests/conftest.py

## 2026-09-07 — builtin loading moved to the repo-root conftest

Applies to every test tree, not only tests/.

## 2026-10-28 — fresh netmind-login per-IP bucket per test

`backend.routes.auth` now charges a module-level per-IP bucket on
`POST /api/auth/netmind-login` (PR #420 review M-4). Every TestClient request
comes from the same peer (`testclient`), and several test modules
(netmind-login route, account suspension, guide-agent login hook, legacy-auth
removal, body-size gate, signup abuse limits) log in through that route, so a
module singleton would fill across modules and surface as an order-dependent
429 ("green alone, red in the full run"). `_fresh_netmind_login_ip_bucket`
swaps in a fresh limiter at the shipped/env-resolved limit around every test,
and like `_clear_health_cache` it only touches the module if something
already imported it.
