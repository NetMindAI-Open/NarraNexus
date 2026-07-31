---
code_file: frontend/src/components/onboarding/MigrationGuide.tsx
last_verified: 2026-07-30
stub: false
---

# onboarding/MigrationGuide.tsx — one-time "import your other agents" guided flow

## Why it exists

Replaces the old MigrationNudge banner with a guided, once-per-user flow
(Owner's design). On the local app, if other-framework agents are detected, show
a welcome modal → [Import] opens [[ImportAgentModal]], [Later]/X points the user
at the sidebar "+" via a coach-mark. Mounted by [[MainLayout]].

## Design decisions

- **Local only** (`mode === 'local'`); cloud has no user filesystem.
- **Per-user, once** (`lib/migrationGuide` localStorage keyed by userId): the
  welcome modal shows once ever per user (`welcomed`). Detect only runs while
  `!welcomed`.
- **State machine**: `welcomed` (modal actioned) → set on any modal action;
  `coachmarkPending` (Later/X) → show coach-mark; `coachmarkDone` (clicked away).
  The coach-mark shows while `welcomed && coachmarkPending && !coachmarkDone`, so
  it survives reloads until clicked ("挂到点掉").
- **Modal shows framework + count only** ([[MigrationCoachmark]] the real per-
  project/session picking stays in ImportAgentModal). The `global-shared-config`
  fallback detection is excluded from the count.
- Persisted state is **read directly in render** (localStorage is cheap) with a
  setter-only re-render trigger, not stored in state + a setState-in-effect —
  keeps it lint-clean and always fresh.

## Gotcha

- [Import] sets `welcomed` but NOT `coachmarkPending`, so choosing Import never
  shows the coach-mark (no need to point someone who already went to import).
