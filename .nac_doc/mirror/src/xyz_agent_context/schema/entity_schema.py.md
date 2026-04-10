---
code_file: src/xyz_agent_context/schema/entity_schema.py
last_verified: 2026-04-10
stub: false
---

# entity_schema.py

## Why it exists

This file consolidates four "core entity" domain models — `SocialNetworkEntity`, `User`, `Agent`, and `MCPUrl` — into one place. These are the objects that map directly to rows in the primary business tables (`instance_social_entities`, `users`, `agents`, `mcp_urls`). Centralizing them here means the repository layer and the route layer both import from a single canonical location rather than defining local versions.

## Upstream / Downstream

`SocialNetworkRepository` serializes/deserializes `SocialNetworkEntity`. `UserRepository` uses `User`. `AgentRepository` uses `Agent`. `MCPRepository` uses `MCPUrl`. On the API side, `api_schema.py` projects `SocialNetworkEntity` into `SocialNetworkEntityInfo` (a subset for the frontend) and `MCPUrl` into `MCPInfo`. The repositories are the only path to the database; the domain models here should never be written to the database by any other code.

## Design decisions

**`SocialNetworkEntity.instance_id` instead of `owner_agent_id`**: a refactoring in December 2025 changed the ownership model so that social network data follows a `SocialNetworkModule` *instance*, not directly an agent. This allows the same agent to have separate social graphs for different narrative contexts. Old code that tried to query by `agent_id` directly will silently miss records unless it first resolves the relevant `instance_id`.

**`SocialNetworkEntity.embedding` is stored inline in the entity row**: this was the original design. Later, `EmbeddingStoreRepository` was introduced as a normalized embedding store. For entities, there is now a dual-path: old vectors live in the `embedding` column, new vectors live in the `embeddings_store` table. `SocialNetworkRepository.semantic_search()` uses a bridge flag (`use_embedding_store()`) to choose which path to read from.

**`UserStatus.DELETED`** is a soft-delete marker, not a hard delete. The `UserRepository.delete_user()` method defaults to `soft_delete=True`, which just sets this status. The row stays in the database so foreign-key-like references in other tables remain valid.

**`Agent.is_public`** controls whether non-creator users can see and interact with an agent in the UI. This is an application-level visibility flag, not a database permission.

## Gotchas

**`MCPUrl` vs `MCPInfo`**: `MCPUrl` has `mcp_id`, `agent_id`, `user_id`, and the full connection state fields. `MCPInfo` in `api_schema.py` has all the same fields. The two are structurally identical by convention but are separate classes — changes to one do not propagate to the other automatically.

**`SocialNetworkEntity.tags` and `expertise_domains`** are both `List[str]` but they serve different purposes. `tags` are freeform descriptors used for keyword search (e.g., `"expert:recommendation_system"`). `expertise_domains` are normalized domain labels used for intelligent matching (e.g., `"recommendation_system"`). It is easy to put the same string in both by mistake; only `tags` is searched by `JSON_SEARCH` in `search_by_tags()`.

## New-joiner traps

- `Agent.agent_metadata` and `User.metadata` are both `Optional[Dict[str, Any]]` but stored as JSON strings in MySQL. `AgentRepository` and `UserRepository` each have their own `_parse_json_field()` static method to handle the conversion. Do not read these fields raw from a database cursor — always go through the repository.
- `SocialNetworkEntity.persona` is a natural language string (not structured data) describing how to communicate with this person. It is written by the agent during entity updates and read back into the system prompt context. Do not confuse it with `identity_info` which is structured JSON.
