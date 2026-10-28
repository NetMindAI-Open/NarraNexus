---
code_file: frontend/src/lib/guideCoachmark.ts
last_verified: 2026-08-19
stub: false
---

# guideCoachmark.ts — localStorage gate for the new-user coachmark

## Why it exists

Brand-new users get their first agent auto-provisioned server-side, so they
never meet the create button — the one-shot coachmark fixes that. The gate
lives in `lib/` (not the component) because the ARM side is the api layer
(`api.netmindLogin` sees `is_new_user`; `api.createUser` implies new) and the
SHOW side is a component, and api.ts must not import from components/.

## Design decisions

- Three states in one key (`nx-guide-coachmark`): absent → 'pending' →
  'done'. `markGuideCoachmarkPending` never downgrades 'done' (a re-login of
  the same new user must not resurrect a dismissed coachmark).
- All three helpers swallow storage failures: the greeting text carries the
  same hint, so a private-mode browser just misses the bubble.

## Gotchas

- localStorage is per-browser, not per-account: a second new account on the
  same browser after a dismissal shows nothing. Accepted — the greeting
  covers it.
