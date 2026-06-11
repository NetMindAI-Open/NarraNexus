---
code_file: src/xyz_agent_context/memory/backfill.py
last_verified: 2026-06-09
stub: false
---

# backfill.py — re-project operational rows into the search indexes

## Why it exists

The live writers (crud._index_narrative / step_4 interaction / create_job /
send_message) only index data they CREATE. Data that predates the unified-memory
search layer — sitting in a long-lived DB, or raw-inserted by a bundle import —
has no index and is invisible to `remember`. This module rebuilds those indexes,
composing the SAME searchable text + source_ref each live writer produces. It is
the single shared implementation behind TWO callers, so the logic lives in one
place:

- bundle import → `backfill_agent_search_indexes` per freshly imported agent
  (see [[importer]]).
- migration 0001 → the same function for every agent in the DB, plus
  `migrate_legacy_entities` (see [[m0001_unified_memory_backfill]]).

## What it does

- `backfill_agent_search_indexes(db, agent_id)`: re-indexes narrative / job / bus
  / interaction(event) for one agent. entity is handled separately; observation
  is LLM-distilled and unreconstructable — both out of scope.
- `migrate_legacy_entities(db)`: moves retired `instance_social_entities` rows
  into memory_entity via `SocialNetworkRepository.save_entity` (the current
  record-id scheme + derived content_text, so entities are findable by NAME).

## Design decisions

- **Idempotent**: `engine.index` and `save_entity` use deterministic record_ids
  → upsert. Safe on every import and every startup until the ledger records it.
- **Resilient**: a single bad row is logged and skipped (`_idx` try/except),
  never aborting the rest.
- **Faithful text**: each kind's content_text mirrors its live writer exactly, so
  backfilled rows rank identically to freshly written ones.

## Gotchas

- bus indexes only messages the agent SENT (`from_agent`), matching
  `local_bus.send_message`.
- interaction text = `env_context.input` + `final_output`, matching step_4.
