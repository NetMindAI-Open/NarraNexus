---
code_file: src/xyz_agent_context/schema/agent_message_schema.py
last_verified: 2026-04-10
stub: false
---

# agent_message_schema.py

## Why it exists

This schema is the persistent record of every message that flows through an Agent's conversation stream — both incoming (user messages) and outgoing (agent replies). It exists so that any channel (direct chat, Job triggers, Matrix bridge) writes messages into a single normalized table (`agent_messages`), and later the narrative system can look up which Narrative and Event a message ultimately resolved to.

The table acts as an audit trail: a message first lands with `if_response=False`, and after the agent finishes its execution loop the record is updated to carry the `narrative_id` and `event_id` that the reply was generated under.

## Upstream / Downstream

`AgentMessageRepository` is the sole data-access path to this model. `ChatModule` creates messages via `AgentMessageRepository.create_message()` when the user sends input. After `AgentRuntime` finishes executing, it calls `update_response_status()` to stamp the resolved `narrative_id` and `event_id` back onto the row. The frontend's simple chat history endpoint reads these records to build the flat message list shown in the UI.

`MessageSourceType` is used by `AgentMessageRepository.get_unresponded_messages()` to poll for un-replied user messages — this is the heartbeat mechanism for channels that push messages asynchronously (e.g., Matrix, MessageBus).

## Design decisions

**Why a dedicated table rather than deriving message history from the `events` table?** Events only exist after the agent has responded. User messages that arrive while the agent is busy, or that never trigger a response (edge cases), would be invisible. The `agent_messages` table guarantees every incoming message is captured regardless of what the agent does with it.

**`if_response` as a boolean flag rather than a status enum**: the only states that matter operationally are "pending reply" and "replied". Introducing more states (e.g., "processing", "error") was rejected as premature — it would complicate the poller and add no practical value.

## Gotchas

**`narrative_id` and `event_id` are `None` at insert time**. This is by design, but it means any query that joins on these fields after creation will find nulls until the agent replies. Do not treat null as "missing data" — treat it as "reply in flight".

**`source_id` is a free-form string** that means different things depending on `source_type`: for `USER` it is the `user_id`, for `AGENT` it is the `agent_id`, for `SYSTEM` it is the literal string `"system"`. There is no foreign-key enforcement; the repository trusts the caller to pass the correct type-id pair.

## New-joiner traps

- `AgentMessage.id` is the database auto-increment integer; `message_id` is the business key used everywhere in application code (format: `amsg_<12hex>`). Never use `id` to look up messages from application code — use `message_id`.
- Confusingly, `MessageSourceType.AGENT = "agent"` does not mean the message was sent by the current agent to the user. It means an agent sent it — this covers both the current agent's outgoing replies and messages received from other agents in an A2A context.
