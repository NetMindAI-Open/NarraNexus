---
code_file: src/xyz_agent_context/repository/agent_repository.py
last_verified: 2026-04-10
stub: false
---

# agent_repository.py

## Why it exists

`AgentRepository` is the only sanctioned path to the `agents` table. Agent records are created by the API, updated by the settings panel, and read by every flow that needs the agent's name, description, or public visibility flag. Centralizing this access prevents the `agents` table from being queried ad-hoc across the codebase.

## Upstream / Downstream

Agent management routes in `backend/routes/` create and update agents via this repository. `BasicInfoModule.hook_data_gathering()` reads `agent_name`, `agent_description`, and `created_by` to populate `ContextData`. Auth middleware reads agent records to verify ownership. The entity model is `schema.entity_schema.Agent`.

## Design decisions

**`id_field = "id"`** (the auto-increment integer) rather than `"agent_id"`: the `agents` table was designed with an auto-increment `id` as the primary key; `agent_id` is a business identifier in a VARCHAR column. Because `id_field = "id"`, `BaseRepository.get_by_id()` is effectively useless here — it would query by the numeric ID. The repository exposes `get_agent()` instead, which queries by `agent_id`.

**`update_agent()` builds raw SQL**: the base class `update()` uses `id_field` (= `"id"`, the integer) but we need to update by `agent_id` (the business key). This is the pattern used throughout the codebase whenever the update condition differs from the base class's assumption.

## Gotchas

**`is_public` stored as integer 0/1 in MySQL**: `_entity_to_row()` converts `bool` to `int(entity.is_public)` on write, and `_row_to_entity()` converts via `bool(row.get("is_public", 0))` on read. Raw integer `1` from a DB cursor is not the same as Python `True` for strict equality checks.

**`bootstrap_active` does not exist in the `agents` table**: it is computed at request time by checking the AwarenessModule state. Do not look for it in this repository.

## New-joiner traps

- Calling `repo.get_by_id("agent_abc123")` will query `WHERE id = 'agent_abc123'` (integer column, string argument) and silently return `None`. Always use `repo.get_agent("agent_abc123")`.
- There is no `delete_agent()` method here. Agent deletion is a cascade operation in the route handler that touches many tables. It cannot safely be handled through a single repository call.
