---
code_file: src/xyz_agent_context/module/narramessenger_module/narramessenger_context_builder.py
stub: false
last_verified: 2026-06-18
---

## Why it exists

Assembles the per-turn execution prompt for a NarraMessenger message via the
`ChannelContextBuilderBase` Template Method. Unlike Telegram (which keeps its
own `bus_messages` history), NarraMessenger ships conversation context INLINE
in every invocation, so this builder reads straight from `ParsedMessage.raw`.

## Design decisions

- **`get_conversation_history` reads the invocation payload**: group →
  `group_context.history_messages`; DM → `context` (`[{role, sender, content}]`).
  Either is normalised to `[{sender, timestamp, body}]`. A trailing entry that
  duplicates the current trigger message is dropped (the template renders the
  current message separately).
- **`get_room_members` reads `group_context.members`** (matrix_user_id +
  display_name). DM → empty (base hides the members section for ≤2 anyway).
- **`send_tool_name="narra_send"`** + a `reply_instruction` telling the agent
  to call `narra_send(room_id=..., text=...)`.
- **`my_channel_id = credential.matrix_user_id`** so "Me (agent)" rows in
  history are labelled correctly.

## Upstream / downstream

- **Upstream**: `ChannelContextBuilderBase` (`build_prompt` / `build_retrieval_anchor`).
- **Reads**: `ParsedMessage.raw` (the full invocation), the credential's
  `matrix_user_id`.

## Gotchas

- History fidelity depends entirely on what the platform put in `context` /
  `group_context`. `compressed_context` may be `null` and `context_state` may
  flag gaps — for v1 we use whatever is provided and do not call
  `/agent-context/group` separately (gateway already inlines it).
