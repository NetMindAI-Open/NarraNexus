---
code_file: src/xyz_agent_context/repository/event_repository.py
last_verified: 2026-04-10
stub: false
---

# event_repository.py

## Why it exists

An `Event` is the atomic record of a single agent execution turn: what triggered it, which module instances were active, what tools were called (event_log), and what the agent finally said (final_output). `EventRepository` is the persistence layer for this record, handling the non-trivial JSON serialization required because `Event` contains nested objects (list of `EventLogEntry`, list of `ModuleInstance`).

## Upstream / Downstream

`NarrativeService._narrative_impl/` creates `Event` records at the start of each AgentRuntime execution and updates them when execution completes via `update_final_output()`. The chat history API reads events via `get_by_narrative()` and `get_by_agent_user()`. `EventLogResponse` in `api_schema.py` is built from event records for lazy-loaded tool call detail views.

## Design decisions

**`id_field = "event_id"`**: events have a string business primary key (`evt_<8hex>`) and this is the field used for all base-class operations. Unlike `AgentRepository` and `AgentMessageRepository`, there is no mismatch here — `event_id` is both the primary identifier in code and the primary key in the database.

**`update_final_output()` serializes `event_log` and `module_instances` in-place**: rather than loading the full event, mutating it, and calling `save()`, this method accepts the final output components directly and builds a targeted update dict. This avoids the expensive round-trip of deserializing then re-serializing the existing event_log just to append to it.

**`ModuleInstance` snapshots in the event record**: the event stores a JSON snapshot of which module instances were active during this execution (not just their IDs). This is deliberate — it creates an immutable audit record. If an instance is later archived or its description changes, the event still reflects what was true at execution time.

## Gotchas

**`_row_to_entity()` calls `ModuleInstance(**m)` for each item in `module_instances`**: this uses the legacy `ModuleInstance` from `module_schema.py` (imported at the top of the file). The legacy class does not have `routing_embedding`, `keywords`, or `topic_hint`. If an event was saved with a `ModuleInstance` snapshot that included those fields (from `instance_schema.ModuleInstance`), the extra fields will be silently dropped on deserialization.

**`event_log` entries are `EventLogEntry` objects from `narrative/models.py`**: `EventRepository` imports directly from `narrative/models.py`. This is one of the few cases where the repository layer reaches into a domain module. The event log structure is tightly coupled to how `AgentRuntime` records tool calls and steps.

**`get_by_narrative()` returns events ordered `DESC` (newest first)**: this is for the UI which shows the most recent activity. If you iterate the list expecting chronological order for replay or analysis, reverse it first.

## New-joiner traps

- `env_context` is a JSON dict stored in the event. It contains arbitrary runtime context (timezone, user preferences) captured at execution start. Its schema is not enforced anywhere — it can have any keys.
- `update_narrative_id()` updates `narrative_id` on an event. This happens in Step 5 of AgentRuntime when the event is retroactively assigned to the Narrative that was selected or created for this turn. Events start with `narrative_id=None` and get patched.
