---
code_file: src/xyz_agent_context/repository/agent_message_repository.py
last_verified: 2026-04-10
stub: false
---

# agent_message_repository.py

## Why it exists

`AgentMessageRepository` manages the `agent_messages` table, which is the inbox/outbox audit trail for every message flowing through an agent. It provides the primary-query contract for the async message bus pattern: channels push messages in, the execution pipeline reads unreplied messages in FIFO order, and after execution the pipeline stamps `narrative_id` and `event_id` back onto each record.

## Upstream / Downstream

`ChatModule` writes incoming messages via `create_message()`. `AgentRuntime` (or a future message bus) reads pending messages via `get_unresponded_messages()` and then calls `update_response_status()` or `batch_update_response_status()` after execution. The simple chat history API route reads `agent_messages` (filtered by `source_type`) to build the user-facing message list.

## Design decisions

**`id_field = "id"`** (auto-increment integer), not `"message_id"`: same pattern as `AgentRepository`. `get_by_id()` on the base class is not used externally. All external lookups go through `get_message()` which queries by `message_id`. Updates use `update()` from the base class — but that also uses `id_field = "id"`. The `update_response_status()` method calls `self.update(message_id, update_data)` — this calls `BaseRepository.update(entity_id=message_id, ...)` which generates `WHERE id = message_id`. **This is wrong** — it should be `WHERE message_id = message_id`. In practice it works only because the base class `update()` calls `self._db.update(table, filters={self.id_field: entity_id}, ...)` so if `id_field` is `"id"` and we pass `message_id`, the SQL becomes `WHERE id = 'amsg_xxx'` which will match zero rows.

Actually looking at the code: `update_response_status()` calls `self.update(message_id, update_data)` from `BaseRepository.update()`. `BaseRepository.update()` uses `{self.id_field: entity_id}` as the filter — so `{"id": "amsg_xxx"}`. This will silently update 0 rows because `id` is an integer. The repository instead builds a manual `batch_update_response_status()` that issues correct SQL. Single-message updates through `update_response_status()` have this latent bug.

**`batch_update_response_status()` uses raw SQL with `IN` clause**: because `update()` from the base class can only filter on one row at a time using `id_field`, bulk updates require raw SQL. This is a correct bypass of the base class.

## Gotchas

**`get_unresponded_messages()` orders `ASC` (oldest first)** — FIFO. All other `get_messages()` calls default to `DESC` (newest first). Be explicit about order when fetching messages for processing vs for display.

**Single-message `update_response_status()`** has a subtle bug: `self.update(message_id, ...)` where `id_field = "id"` means the WHERE clause uses the integer `id` column, not `message_id`. In practice, most callers use `batch_update_response_status()`. If you need to update a single message's status reliably, use `batch_update_response_status()` with a one-element list.

## New-joiner traps

- `AgentMessage.message_id` (business key, `"amsg_<12hex>"`) is different from `AgentMessage.id` (database integer). The repository uses `message_id` in its method signatures but internally `id_field = "id"` creates a mismatch for base-class methods.
- `delete_message()` and `delete_agent_messages()` issue raw SQL deleting by `message_id` or `agent_id` respectively — these work correctly and bypass the broken base class update pattern.
