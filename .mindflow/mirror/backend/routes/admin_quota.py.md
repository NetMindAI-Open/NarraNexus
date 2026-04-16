---
code_file: backend/routes/admin_quota.py
stub: false
last_verified: 2026-04-16
---

# Intent

Staff-only quota management surface. Kept in a dedicated router file so
non-staff users physically cannot reach these paths except through the
`role != "staff"` → 403 check. No UI yet; staff invoke via curl / postman
or a future admin dashboard.

## Endpoints

- `POST /api/admin/quota/grant` — upsert: creates row with `initial=0` if
  missing, then credits `granted_* += delta`. An `exhausted` user is
  flipped back to `active` when the credit brings remaining above zero.
  Staff doesn't need to distinguish "new vs pre-feature" user.
- `POST /api/admin/quota/init` — applies the env-configured initial
  allocation (SYSTEM_DEFAULT_QUOTA_*) to a user. Idempotent — if the
  user already has a row it is returned unchanged (no overwrite).

## Auth

Both routes require:
1. Cloud mode (503 in local — admin surface doesn't exist there)
2. A valid JWT set by `auth_middleware` on `request.state.user_id`
3. `role == "staff"` (403 otherwise)

## Gotchas

- `user_repository` is taken from `app.state.user_repository`; lifespan
  must wire it. Returning 503 if it is missing surfaces wiring bugs
  loudly rather than 500-ing with an obscure AttributeError.
- `init` returns 500 if `init_for_user` returned None *after* the
  feature was confirmed enabled — that path means the repo insert
  failed silently. The log will carry the real reason.
- `grant` is intentionally permitted even when the user already has
  reached the status=disabled state. Staff may want to credit tokens
  even if the user is disabled for another reason; re-enabling is a
  separate consideration.
