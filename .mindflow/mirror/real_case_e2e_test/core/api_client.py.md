---
code_file: real_case_e2e_test/core/api_client.py
last_verified: 2026-05-13
stub: false
---

# api_client.py — narrow async REST wrapper

## Why it exists

Cases interact with the local backend via fixtures, but fixtures need
something to call. APIClient holds just the surface fixtures need:
health probe, `/api/auth/create-user`, `/api/auth/agents`,
`/api/auth/agents/{id}` (DELETE), `/api/providers` (list, used by
preflight).

## Decisions

- One client per case, not shared. Concurrent cases that shared a
  client would also share the httpx connection pool's TLS / cookies /
  base URL state — isolation by construction matters more than the
  micro-overhead of building one client per case.
- Two error classes: `APIError` for HTTP-level failures (caller can
  branch on `.status`), `APILogicError` for `200 success=False` (where
  the body is the only useful diagnostic).
- Local-mode user_id is always passed explicitly. We never assume the
  backend's "first user wins" — when teams.py honours the query param
  as the rest of local mode already does, no code in this module
  changes.
