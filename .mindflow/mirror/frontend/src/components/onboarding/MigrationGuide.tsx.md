---
code_file: frontend/src/components/onboarding/MigrationGuide.tsx
last_verified: 2026-08-27
stub: false
---

# onboarding/MigrationGuide.tsx — the "import lives behind +" coach-mark gate

## Why it exists

Mounted by [[MainLayout]]. Since 2026-08-27 (Owner decision) this component no
longer detects anything and no longer shows a dialog: the import offer became
step 2 of the first-run flow ([[StepImport]]). What survived is the follow-up for
users who SKIP that step — a bubble pointing at the sidebar "+" so they still
learn where import went.

So the whole component is now a gate around [[MigrationCoachmark]], and
[[WelcomePage]] is the only thing that arms it.

## Design decisions

- **Local only** (`mode === 'local'`): pointing at a feature that 503s on cloud
  would be a lie.
- **Per-user localStorage** ([[migrationGuide]]), keyed by userId and read once
  through a keyed inner component, so a shared machine hints each user
  separately.
- `welcomed` is gone from the state: "has this user been offered the import" is
  now the server-side `onboarding_progress.landing_completed`, not a browser flag.
