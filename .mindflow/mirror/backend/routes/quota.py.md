---
code_file: backend/routes/quota.py
stub: false
last_verified: 2026-04-16
---

# Intent

Read-only quota view for the signed-in user. Discriminated response shape
lets the frontend avoid duplicating "is the feature on" logic:

- `{enabled: false}` — local mode OR SystemProviderService disabled
- `{enabled: true, status: "uninitialized"}` — cloud user without a quota
  row yet (pre-feature registration; staff can run the migration script
  or call /api/admin/quota/init)
- `{enabled: true, status: "active"|"exhausted"|"disabled", …}` — full
  breakdown, matching the `Quota` schema's public fields.

## Upstream
- Frontend `QuotaPanel` component (polls on page load; hides when
  `enabled == false`)
- Frontend RegisterPage (confirms seed success via round-trip after
  register response signals `has_system_quota: true`)

## Downstream
- `app.state.system_provider.is_enabled()` — primary gate
- `app.state.quota_service.get()` — one read, no write

## Gotchas
- Returns `{enabled: false}` when the services are not yet wired
  (`app.state.system_provider` missing). This is only possible during
  test harness setup before `lifespan` runs; production always wires.
- Does NOT call `require_auth` explicitly — relies on `auth_middleware`
  already having populated `request.state.user_id`. In cloud mode the
  middleware enforces JWT before this route runs; in local mode
  `is_cloud_mode()` returns False and we short-circuit before touching
  user_id at all.
