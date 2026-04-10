---
code_file: src/xyz_agent_context/repository/narrative_repository.py
last_verified: 2026-04-10
stub: false
---

# narrative_repository.py

## Why it exists

`NarrativeRepository` manages the `narratives` table, which is the memory backbone of the system. Each Narrative is a long-running conversation thread with a summary, a set of active module instances, and a growing list of events. It is the most complex repository because a Narrative row contains many nested JSON columns that each serialize entire object graphs.

## Upstream / Downstream

`NarrativeService` (via `_narrative_impl/`) is the primary consumer: it creates, updates, and retrieves Narratives as part of the selection and summarization flow. The `EmbeddingStore` migration bridge reads `get_with_embedding()` to populate the centralized embedding table. The chat history API reads narratives via `get_by_agent_user()` for context display.

## Design decisions

**`get_by_agent_user()` filters in application memory**: `user_id` is embedded inside the `narrative_info.actors` JSON blob rather than as a top-level column. The database cannot index into a JSON array efficiently for this query. The repository fetches `limit * 2` rows by `agent_id` and then filters in Python. This means the effective limit is approximate — if many narratives don't contain the user, fewer than `limit` results may be returned.

**`get_narratives_by_participant()` uses MySQL `JSON_CONTAINS`**: for the "participant" role (e.g., a target customer in a sales scenario), there is a different code path using `JSON_CONTAINS(JSON_EXTRACT(narrative_info, '$.actors'), ...)`. This is server-side JSON filtering. It is more efficient than the Python-side filtering in `get_by_agent_user()` but requires MySQL 5.7+.

**`count_default_narratives()` and `get_default_narratives()` use `LIKE` on `narrative_id`**: default narratives follow the naming pattern `{agent_id}_{user_id}_default_*`. Using `LIKE` on the string ID is a pragmatic choice that avoids an extra `is_default` boolean column.

**`routing_embedding` stored as JSON in the narratives table**: as of the time this repository was written, embeddings were stored inline. A migration to `embeddings_store` table was added later (via `EmbeddingStoreRepository`). Both paths may exist in production data simultaneously.

## Gotchas

**`main_chat_instance_id` is marked deprecated** in the code (comment: "2026-01-21 P1-1: deprecated"). The field exists in `_row_to_entity()` and is set to `None` in `_entity_to_row()`. Old data may have non-null values; new saves do not write it. Code that reads `narrative.main_chat_instance_id` may get `None` even for narratives that formerly had it set.

**`active_instances` in the Narrative row is a snapshot** — the same dual-representation problem as in `EventRepository`. The authoritative list of instances per narrative is in the `instance_narrative_links` table. The `active_instances` JSON blob in the narratives table is a denormalized cache. If they drift out of sync, `InstanceNarrativeLinkRepository` is the truth.

**`_parse_datetime_field()` is a separate helper** only in `NarrativeRepository`, not in `BaseRepository`. This is because `embedding_updated_at` can arrive as a string from some code paths. Other repositories don't have this problem.

## New-joiner traps

- The `narratives` table `type` column maps to `NarrativeType` enum from `narrative/models.py`. The repository imports from `narrative/models.py` directly, making it one of two repositories (along with `event_repository`) that depend on the narrative domain layer. This import direction is acceptable because narratives are fundamentally part of the narrative domain.
- `get_with_embedding()` loads all narratives for an agent (up to `limit * 2`) and filters in Python based on whether `routing_embedding` is non-empty. For agents with many narratives this can be expensive. Use only when you need embedding-based retrieval.
