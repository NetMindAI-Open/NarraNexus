---
code_dir: src/xyz_agent_context/repository/
last_verified: 2026-04-10
stub: false
---

# repository/

## Directory role

This directory is the data access layer — the only code that is allowed to issue SQL queries. Every class here extends `BaseRepository[T]` (except `EmbeddingStoreRepository`) and is responsible for converting between Python domain objects and MySQL rows, including JSON serialization/deserialization of nested fields.

No business logic lives here. No imports from `module/`, `agent_runtime/`, or `narrative/` are allowed, with two deliberate exceptions: `EventRepository` and `NarrativeRepository` import from `narrative/models.py` because those domain models are the entity types they persist.

## Key file index

| File | Table | Entity type |
|---|---|---|
| `base.py` | — | BaseRepository[T] generic base |
| `agent_repository.py` | `agents` | `entity_schema.Agent` |
| `user_repository.py` | `users` | `entity_schema.User` |
| `event_repository.py` | `events` | `narrative.models.Event` |
| `narrative_repository.py` | `narratives` | `narrative.models.Narrative` |
| `instance_repository.py` | `module_instances` | `instance_schema.ModuleInstanceRecord` |
| `instance_link_repository.py` | `instance_narrative_links` | `instance_schema.InstanceNarrativeLink` |
| `instance_awareness_repository.py` | `instance_awareness` | local `InstanceAwareness` dataclass |
| `job_repository.py` | `instance_jobs` | `job_schema.JobModel` |
| `inbox_repository.py` | `inbox_table` | `inbox_schema.InboxMessage` |
| `agent_message_repository.py` | `agent_messages` | `agent_message_schema.AgentMessage` |
| `social_network_repository.py` | `instance_social_entities` | `entity_schema.SocialNetworkEntity` |
| `rag_store_repository.py` | `instance_rag_store` | `rag_store_schema.RAGStoreModel` |
| `mcp_repository.py` | `mcp_urls` | `entity_schema.MCPUrl` |
| `embedding_store_repository.py` | `embeddings_store` | raw dict (no entity class) |

## Recurring patterns and gotchas

**The `id_field` mismatch pattern**: several repositories set `id_field = "id"` (the auto-increment integer) even though their business key is a different column (`agent_id`, `message_id`, `mcp_id`). This means `BaseRepository.get_by_id()` and `BaseRepository.update()` are broken for those repositories. They compensate by providing custom `get_X()` and `update_X()` methods that build raw SQL targeting the business key. Repositories where `id_field` matches the business key: `EventRepository` (`event_id`), `InstanceRepository` (`instance_id`), `JobRepository` (`job_id`), `NarrativeRepository` (`narrative_id`), `InstanceAwarenessRepository` (`instance_id`).

**JSON field handling**: MySQL stores complex fields as JSON strings. Every repository has a `_parse_json_field()` static method (duplicated, not shared from BaseRepository). `SocialNetworkRepository._parse_json_field()` has an extra double-encoding guard.

**Raw SQL bypasses**: whenever a query requires OR conditions, compound keys in WHERE clauses, or JOIN-like patterns, repositories bypass `BaseRepository.find()` and call `self._db.execute()` directly. This is by design — the base class only handles simple equality filters.

## Collaboration with other directories

- `schema/` provides the entity model classes that repositories deserialize into and serialize from.
- `utils/database_table_management/` contains the `CREATE TABLE` and `ALTER TABLE` scripts that define the tables managed here.
- Services in `narrative/`, `module/`, and `services/` are the primary consumers — they instantiate repository classes with a shared `AsyncDatabaseClient` and call their methods.
- `backend/routes/` should not instantiate repositories directly — it goes through the service layer.
