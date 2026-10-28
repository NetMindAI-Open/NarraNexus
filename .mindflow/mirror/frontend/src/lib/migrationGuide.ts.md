---
code_file: frontend/src/lib/migrationGuide.ts
last_verified: 2026-08-27
stub: false
---

# lib/migrationGuide.ts — persistence for the import coach-mark

## Why it exists

The localStorage state behind [[MigrationGuide]], keyed by userId (a shared
machine hints each user once). Local/desktop only, so per-machine-per-user is the
right scope — no backend round-trip for a UI hint.

## Shape

`{ coachmarkPending, coachmarkDone }` under `nn_migration_guide:<userId>`.
`read` tolerates missing/corrupt JSON (returns defaults); `write` merges a patch
and degrades to in-memory on storage failure.

## Design decisions

- **2026-08-27: `welcomed` removed.** "Has the user been offered the import?" is
  now answered server-side by `onboarding_progress.landing_completed`, so a
  second browser doesn't replay the offer. This file keeps only the bubble's own
  two flags — armed by [[WelcomePage]] when the user skips the import step.
