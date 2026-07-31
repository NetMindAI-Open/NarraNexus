---
code_file: frontend/src/types/migration.ts
last_verified: 2026-07-21
stub: false
---

# types/migration.ts — Frontend types for Agent Migration

## Why it exists

TypeScript mirror of the Agent Migration standardized JSON contract
(`src/xyz_agent_context/schema/migration_schema.py`) plus the `ApplyResult`
returned by `POST /api/migrate/apply`. Consumed by [[api]] (the migrate*
methods) and [[ImportAgentModal]].

## Gotchas

- Must stay in **lock-step** with the Python schema: `/scan` returns a
  `StandardizedAgentImport` that the UI POSTs back to `/apply` unchanged, so a
  drift between the two shapes silently drops fields on the write path.
- Re-exported through the `@/types` barrel ([[index]]).
