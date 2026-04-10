---
code_file: src/xyz_agent_context/repository/inbox_repository.py
last_verified: 2026-04-10
stub: false
---

# inbox_repository.py

## Why it exists

`InboxRepository` manages the `inbox_table`, which holds notifications delivered to users by the agent — primarily Job completion summaries. It provides the read/write interface for the inbox feed: creating messages, marking them read, paginating the list, and filtering by source or type.

## Upstream / Downstream

`JobModule.hook_after_event_execution()` calls `create_message()` when a Job completes and `should_notify=True`. The inbox API route reads messages via `get_messages()` and `get_total_count()` for pagination, and marks messages read via `mark_as_read()` and `mark_all_as_read()`. The frontend notification badge reads `get_unread_count()`.

## Design decisions

**`id_field = "id"`** (auto-increment): same mismatch pattern as `AgentRepository`. All meaningful operations use `message_id`-based raw SQL methods. The base class CRUD methods are not useful here.

**`get_messages()` has two code paths**: the standard filter path uses `BaseRepository.find()` for most filters. But when `source_type` is specified, it falls back to raw SQL with `JSON_EXTRACT(source, '$.type')` because the `source` field is a JSON blob and the base class `find()` only supports equality on scalar columns.

**`source` stored as JSON string**: the `MessageSource` object is serialized to a JSON string in the database (e.g., `'{"type": "job", "id": "job_abc"}'`). The `_parse_json_field()` helper in `_row_to_entity()` deserializes it. This means querying by source.type requires `JSON_EXTRACT`, which prevents the filter from using a traditional B-tree index.

**`get_total_count()` for pagination**: the inbox API paginates messages with offset/limit. `get_total_count()` provides the total count for the current filter combination so the frontend can render the correct page count. This is a separate query from the page fetch.

## Gotchas

**The table name is `inbox_table`** with the `_table` suffix. Raw SQL queries targeting `inbox` (without the suffix) will fail silently if the MySQL user has access but the table does not exist, or fail loudly with a "table not found" error.

**`mark_as_read()` and `delete_message()` query by `message_id`** (the business key), not by `id` (the auto-increment primary key). The raw SQL in these methods is correct. Do not try to use `BaseRepository.update()` or `BaseRepository.delete()` here — they use `id_field = "id"` and will match the wrong rows.

## New-joiner traps

- `create_message()` requires the caller to supply a `message_id`. There is no auto-generation in the repository. `JobModule` generates the ID before calling this method. Duplicate `message_id` values will cause a database constraint violation.
- `InboxMessageType.CHANNEL_MESSAGE` is for messages delivered via external IM channels (Matrix, Slack). These messages are also written to the inbox for the user to review even after the IM message was delivered.
