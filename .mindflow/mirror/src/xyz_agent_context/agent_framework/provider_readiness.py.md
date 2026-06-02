---
code_file: src/xyz_agent_context/agent_framework/provider_readiness.py
last_verified: 2026-06-01
stub: false
---

# Intent

Framework-level "is this user ready to run right now" check (a live readiness
probe), used by the edge-triggered recovery of PAUSED_NO_QUOTA jobs. It sits
ABOVE the module layer — part of the agent_framework provider stack, owned by no
Module (铁律 #3) — so the job recovery path, backend routes, and future
capabilities all call the same facade.

## Two tiers (why both exist)

- `classify_provider_availability` (provider_resolver) is the CHEAP static
  verdict on the hot path (every job pickup / runtime). It must match the
  runtime exactly — that equivalence is the oscillation fix.
- `ProviderReadiness.validate` here adds a LIVE provider connectivity test on
  top, run only at rare edge events (login / quota grant / preference toggle /
  provider save) where a human action could have just fixed things. "Tested OK
  → recover" beats re-arming a job into another immediate failure. The live test
  is too expensive for the hot path, which is why it lives separately.

## Shape / extension point

`validate(user_id, db) -> (ready, reason)`:
1. static `classify` → not runnable short-circuits (don't live-test a user with
   no budget / no provider);
2. SYSTEM_OK / SYSTEM_DISABLED skip the live test (platform's own provider /
   local passthrough);
3. USER_OK → live-test the agent-slot provider via `UserProviderService.
   test_provider`. A flaky/raising live test does NOT strand the user (falls
   back to ready) — only an explicit test FAILURE blocks recovery.

`validate` is intentionally a thin pipeline so it can grow into a hook
environment (rate-limit / account-status / business-rule readiness hooks slot in
here) without touching callers. Design:
`2026-06-01-job-scheduler-resilience-design.md`.
