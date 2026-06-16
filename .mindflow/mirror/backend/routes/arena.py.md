---
code_file: backend/routes/arena.py
last_verified: 2026-06-15
stub: false
---

# arena.py — Arena onboarding HTTP endpoint

## Why it exists

The single backend entry point for the Arena landing flow: a user who arrives
from arena42.ai (already authenticated via NetMind Power SSO) calls
`POST /api/arena/provision` and is handed a ready-to-play Arena agent. Thin
controller — all the work is in `ArenaProvisioningService`.

## Upstream / Downstream

**Called by:** the frontend `lib/arenaLanding.ts` after login, once
`source=arena` is detected. **Delegates to:**
`ArenaProvisioningService(db).provision(user_id)`. **Auth:** required — the
route is NOT in `AUTH_EXEMPT_PATHS`, so the middleware enforces a session
(cloud: Bearer JWT minted by the inbound-token / netmind-login SSO flow; local:
`X-User-Id`). The user is derived from the session, never the body.

## Design decisions

**No request body.** Identity comes from `resolve_current_user_id(request)`;
the endpoint is "ensure THIS user has an Arena agent." Idempotent and
refresh-safe — the warm path is a single DB read.

**502 on provisioning failure.** Arena registration is an upstream dependency;
a failure there surfaces as `502 Bad Gateway` with the cause, distinct from a
client/auth error.

## Gotchas

- Registered in `backend/main.py` with no extra prefix (the router already
  declares `prefix="/api/arena"`), mirroring the quota router convention.
