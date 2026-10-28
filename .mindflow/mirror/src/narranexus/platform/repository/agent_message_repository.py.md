---
code_file: src/narranexus/platform/repository/agent_message_repository.py
last_verified: 2026-08-05
stub: false
---

## 2026-08-05 — READ THIS FIRST: `agent_messages` has no writer. It is a tombstone.

The "Upstream / Downstream" paragraph below described a design that is **not
what the code does**, and believing it cost a real misdiagnosis: the 0802
【对话时序错乱】 analysis checked `agent_messages`, found **0 rows**, and
concluded the chat panel must therefore be replaying the `events` table. It is
not — see below for where the transcript actually lives.

Verified 2026-08-05 on today's `origin/dev`:

- **Nothing calls `create_message()`.** `git grep AgentMessageRepository` over
  `src/` + `backend/` returns only `repository/__init__.py` (the re-export) and
  one dead import in [[chat_module]] (now removed). Nothing calls
  `get_unresponded_messages()` / `update_response_status()` either.
- **`agent_messages` is 0 rows** locally and in prod.
- The chat transcript lives in **`instance_json_format_memory_chat`**, keyed by
  ChatModule instance id, written by `ChatModule.persist_turn` and replayed
  by `/simple-chat-history` → [[buildTimeline.ts]]. The `events` table is a
  separate surface: one row per agent run, replayed by `/chat-history` into the
  Narrative / Runtime panels.

The table and this repository stay in place as a tombstone (铁律 #6 — no
destructive migration). The delete/export sites that still name it
([[wipe_service]], [[builder]], `auth.delete_agent`) are correct to keep
sweeping it. **Do not** write new code against it without the Owner deciding
the table's future first.

# agent_message_repository.py

## Why it exists

`AgentMessageRepository` was built as the CRUD surface for `agent_messages` —
intended as the inbox/outbox audit trail for every message flowing through an
agent, with a FIFO "read the unreplied ones" contract for an async message-bus
pattern. That pattern was never wired up here (the real one lives in
[[message_bus_trigger]] against its own tables), so the class below is
unreferenced. Read the 2026-08-05 note above before using any of it.

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
