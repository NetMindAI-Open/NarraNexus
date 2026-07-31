---
code_file: frontend/src/lib/migrationGuide.ts
last_verified: 2026-07-30
stub: false
---

# lib/migrationGuide.ts — per-user persistence for the migration guided flow

## Why it exists

The localStorage state for [[MigrationGuide]], keyed by userId (per-user; a shared
machine nudges each user once). localStorage — the whole feature is local/desktop
only, so per-machine-per-user is the right scope, no backend round-trip.

## Shape

`{ welcomed, coachmarkPending, coachmarkDone }` under `nn_migration_guide:<userId>`.
See [[MigrationGuide]] for the state machine. `read` tolerates missing/corrupt
JSON (returns defaults); `write` merges a patch and is a no-op on storage failure.
