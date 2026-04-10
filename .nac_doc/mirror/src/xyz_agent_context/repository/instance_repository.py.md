---
code_file: src/xyz_agent_context/repository/instance_repository.py
last_verified: 2026-04-10
stub: false
---

# instance_repository.py

## Why it exists

`InstanceRepository` manages the `module_instances` table — the registry of all active, completed, and archived module instances across all agents. It is the data layer for Step 2 of `AgentRuntime` (loading candidate instances for selection) and for `ModulePoller` (polling for state transitions). It also implements the in-process vector similarity search used for semantic instance retrieval.

## Upstream / Downstream

`ModuleService._module_impl/` calls `get_by_agent_and_user()` and `vector_search()` to find candidate instances for the current turn. `ModulePoller` polls via `get_by_agent()` filtered on status `in_progress`. `InstanceNarrativeLinkRepository` is the companion repository — instance-narrative links are stored separately and loaded on top of `ModuleInstanceRecord` at runtime.

## Design decisions

**`id_field = "instance_id"`**: unlike `AgentRepository` and `AgentMessageRepository` where `id_field = "id"` creates a mismatch, here `instance_id` is both the business key and the field used as the primary lookup key. `BaseRepository.get_by_id("chat_a1b2c3d4")` works correctly.

**`get_by_agent_and_user()` uses raw SQL** with `(is_public = 1 OR user_id = %s)`: the base class `find()` only supports equality filter dicts. An OR condition requires raw SQL. This is a clean, deliberate bypass.

**`vector_search()` loads all candidates and computes cosine similarity in Python with `numpy`**: MySQL has no native vector index. The decision was to keep it simple and pay the deserialization cost. For small-to-medium agent setups (< a few thousand instances), this is acceptable. At scale it would need a vector database.

**`get_chat_instances_by_user()` explicitly hardcodes `module_class = 'ChatModule'`**: this is a specific query for the "dual-track memory loading" feature (P1-2, January 2026). It retrieves all ChatModule instances for a user across all narratives to load short-term memory from recent non-current conversations.

## Gotchas

**`vector_search()` does not apply `status_filter` before loading candidates**: it first loads all instances for the agent+user via `get_by_agent_and_user()`, then filters by status in Python. For agents with many archived instances, this is wasteful. The SQL queries do not push the status filter to the database.

**`routing_embedding` is stored as JSON and loaded on every `find()` call**: even queries that don't need embeddings (e.g., `get_by_agent()` to check statuses) will deserialize 1536-float lists for every instance that has an embedding. There is no lazy-loading — the full entity is always loaded.

**`update_last_used()` formats the time as a string**: `utc_now().strftime('%Y-%m-%d %H:%M:%S')`. Other repositories also do this. If `utc_now()` has timezone info and the database column expects naive datetime, this formatting strips the tz offset. Verify that the format matches what MySQL expects in your environment.

## New-joiner traps

- `InstanceRepository` returns `ModuleInstanceRecord` objects (no live module bound). Callers that need the live module object must bind it separately — the `ModuleService` does this after loading from the repository.
- `callback_processed` and `last_polled_status` are poller-internal fields stored in the same table. Application code (modules, routes) should never read or write these directly — they are owned by `ModulePoller`.
