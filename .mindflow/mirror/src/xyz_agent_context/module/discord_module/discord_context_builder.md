---
code_file: src/xyz_agent_context/module/discord_module/discord_context_builder.py
stub: false
last_verified: 2026-06-16
---

## Why it exists

Discord's ``ChannelContextBuilderBase`` subclass — assembles the per-turn
execution prompt (message metadata, conversation history, reply
instruction). Sibling of ``slack_context_builder.py``.

## Design decisions

- **Real history via REST.** ``get_channel_messages`` (newest-first) is
  reversed for chronological order — like Slack, unlike Telegram which
  has no history API.
- **Reply instruction names the messaging-first tools.** Points the agent
  at ``discord_reply(channel_id, message_id, text)`` (preferred, inline)
  or ``discord_send(channel_id, text)``, and notes standard markdown
  renders natively + the 2000-char auto-split. ``send_tool_name`` is
  ``discord_send``.
- **``room_type``** is "Direct Message" vs "Group Room" from the raw
  ``is_dm`` flag; ``get_room_members`` returns ``[]`` (guild member lists
  need a privileged intent + pagination, not surfaced in the prompt).

## Upstream / downstream

- **Upstream**: ``ChannelContextBuilderBase`` (Template Method assembly),
  ``DiscordSDKClient`` (history).
- **Downstream**: instantiated by ``DiscordTrigger.create_context_builder``;
  its output feeds ``AgentRuntime`` via the base's ``_build_and_run_agent``.

## Gotchas

- ``reply_instruction`` is plain text the LLM follows — if the MCP tool
  signatures change, update this string or the agent will call the old
  shape.
