---
code_file: src/xyz_agent_context/repository/embedding_store_repository.py
last_verified: 2026-04-10
stub: false
---

# embedding_store_repository.py

## Why it exists

As the system migrated to support multiple embedding models (switching from one default model to user-configurable models), the old pattern of storing embedding vectors inline in entity rows (e.g., `narratives.routing_embedding`, `instance_social_entities.embedding`) became problematic. A model switch would require re-embedding every entity. `EmbeddingStoreRepository` provides a normalized `embeddings_store` table that stores vectors keyed by `(entity_type, entity_id, model)`, enabling lazy migration: new vectors are written to the new table, old vectors stay in entity rows, and the system bridges between the two via `embedding_store_bridge.py` in `agent_framework/`.

## Upstream / Downstream

`agent_framework/llm_api/embedding_store_bridge.py` is the primary consumer — it provides `use_embedding_store()` (a flag) and `get_stored_embeddings_batch()`. When the flag is `True`, callers like `SocialNetworkRepository.semantic_search()` fetch vectors from the central store instead of from entity rows. The embedding generation pipeline writes to this store via `upsert()` and `upsert_batch()`. `NarrativeRepository` and other repositories with inline embeddings are candidates for migration but may still use their own columns in parallel.

## Design decisions

**Does not extend `BaseRepository`**: this is the explicit exception to the "all repositories extend BaseRepository" rule. The entity type for this table is just a raw dict (entity_type + entity_id + model + vector), not a domain object that benefits from the `_row_to_entity` / `_entity_to_row` mapping contract. The overhead of the generic wrapper is not justified.

**`upsert()` uses `INSERT ... ON DUPLICATE KEY UPDATE`**: the table has a unique constraint on `(entity_type, entity_id, model)`. This allows the upsert to be atomic — no race condition between check and write. When a model changes, the new vector for the same entity replaces the old one cleanly.

**`get_entity_ids_missing_model()` for lazy migration**: given a list of all entity IDs, this method returns those that don't yet have a vector for a given model. The migration pipeline calls this to identify which entities need re-embedding without fetching all existing vectors first.

**`upsert_batch()` iterates individual `upsert` calls** rather than using a single multi-row insert: this is simpler and correct, though less efficient for large batches. For the current scale (hundreds of entities per agent) this is acceptable.

## Gotchas

**`get_all_by_model()` returns all embeddings for a given entity type and model** with no pagination. For large knowledge bases this could be a significant result set. Always be aware of how many entities you have before calling this.

**Vectors are stored as JSON strings** (`json.dumps(vector)`) in the `vector` column. On read, the repository checks `isinstance(raw, str)` and calls `json.loads()` if needed. If MySQL returns the column as a Python list (driver-dependent behavior), the branch is skipped. This defensive pattern handles both cases.

## New-joiner traps

- `entity_type` is a free-form string (`"narrative"`, `"job"`, `"entity"`). There is no enum enforcing valid values. If you write a new embedding producer, choose a consistent `entity_type` string and document it — there is no central registry.
- The `source_text` column stores the original text that was embedded. This is optional but strongly recommended for debugging and re-embedding. If you store a vector without `source_text`, you cannot verify what text generated it later.
- The bridge flag `use_embedding_store()` in `agent_framework/` controls whether this table is used. During the migration period, you must check this flag before assuming vectors are in the central store.
