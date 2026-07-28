---
code_file: backend/migrations/m0001_unified_memory_backfill.py
last_verified: 2026-06-09
stub: false
---

# m0001_unified_memory_backfill.py — first versioned migration

## Why it exists

The unified-memory search layer added projection indexes (memory_narrative /
job / bus / event) and folded entities into memory_entity. Databases that
predate it have operational rows with no index — invisible to `remember`. This
migration makes that pre-existing data searchable. It is REGISTRY entry 0001
(see [[__init__]]).

## What it does

For every agent: `backfill_agent_search_indexes` (see [[backfill]]) re-projects
narratives / instance_jobs / bus_messages / events into memory_<kind> with the
same text the live writers produce + a source_ref pointer. Then
`migrate_legacy_entities` moves the retired `instance_social_entities` rows into
memory_entity. `chat` (retired kind) and `observation` (LLM-distilled,
unreconstructable) are intentionally skipped.

## Design decisions

- **Per-agent isolation**: one agent's backfill failure is logged and skipped,
  not fatal to the migration.
- **Idempotent**: deterministic record_ids → re-running upserts the same rows, so
  a re-run after a non-recorded failure is safe.
- Validated at real scale (2026-06-09, copy of a 7.5k-event / 1.9k-entity DB):
  11 agents, 8273 indexes + 1928 entities in ~4.5s; second run a 0.00s no-op via
  the ledger.

## Gotchas

- Entity migration derives agent_id from `module_instances` (entities are
  instance-scoped); an entity whose instance is gone is an orphan and is skipped.
- This is heavy on a large DB (one-time, sync at startup). The ledger guarantees
  it runs once; subsequent startups are a single cheap SELECT.
