---
code_file: src/xyz_agent_context/schema/inbox_schema.py
last_verified: 2026-04-10
stub: false
---

# inbox_schema.py

## Why it exists

This schema represents the agent's outbound delivery mechanism — the "sent mail" side of the chat flow. While `agent_message_schema.py` records incoming messages, `inbox_schema.py` records messages the agent proactively sends to users, typically as completion notifications for background Jobs. Without it, there is no persistence layer for "agent → user" communication that happens outside of a live conversation.

## Upstream / Downstream

`InboxRepository` is the sole persistence path. `JobModule.hook_after_event_execution()` writes `InboxMessage` records when a Job completes and `should_notify=True`. The frontend inbox endpoint reads these records filtered by `user_id`, ordered by `created_at DESC`, to render the notification feed. `InboxMessageType.CHANNEL_MESSAGE` is reserved for messages delivered via Matrix/Slack and then also copied into the inbox for the user to review.

`MessageSource` is a generic source reference embedded as JSON in the database row — it lets readers trace an inbox message back to the originating Job or Event without a hard foreign key.

## Design decisions

**`InboxMessage` belongs to `user_id`, not `agent_id`**: the user receives notifications, not the agent. An agent executing a Job on behalf of user A should write the result to user A's inbox, even if user B set up the agent. This scoping was a deliberate design choice to keep the notification feed personal.

**`source` stored as JSON blob rather than separate `source_type`/`source_id` columns**: the original schema had separate columns but they were consolidated into a single JSON `source` field to keep the table flexible for new source types. `InboxRepository` uses `JSON_EXTRACT(source, '$.type')` to query by source type, which is marginally slower than a column index but avoids schema migrations for new source types.

**`InboxMessageType.AGENT_MESSAGE` and `SYSTEM_NOTICE` are "reserved"**: they exist in the enum to reserve the namespace but no production code currently writes them. Do not build logic that depends on these values being populated.

## Gotchas

**The table is named `inbox_table`** (with the `_table` suffix), not `inbox`. This is a historical naming quirk. `InboxRepository.table_name = "inbox_table"`. If you write a raw SQL query against `inbox` it will fail with "table not found".

**`event_id` and `source` can both be present on the same record** but they overlap in meaning. `source` is the structured provenance (e.g., `{"type": "job", "id": "job_abc"}`); `event_id` is the specific execution event. For Job notifications, `source.id` is the `job_id` and `event_id` is the specific execution `event_id`. They are not redundant: `source` gives you the Job identity for display, `event_id` lets you lazy-load the full event log.

## New-joiner traps

- `InboxMessage.message_id` is the business key (format `inbox_<uuid>` or similar) set by the caller. Unlike `agent_message_schema.py` where the repository generates the ID, here the caller must supply `message_id` to `InboxRepository.create_message()`. Failing to pass a unique ID will cause a duplicate key error.
- `InboxMessageType` values are checked as raw strings in the database. If you add a new enum member and want to filter by it, the old rows in the database are unaffected — they still carry whatever string was stored at creation time.
