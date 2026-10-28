---
code_file: plugins/builtin.channels.discord/src/narranexus_plugins/discord_module/discord_context_builder.py
stub: false
last_verified: 2026-08-06
---

## 2026-08-06 — `room_type` 现在是行为开关

判定逻辑没变（raw 的 `is_dm`），只是换用 `channel_prompts` 的 `ROOM_TYPE_*` 常量。
**语义变了**：这个值现在**选择注入哪份通讯协议**（私聊「默认回复」vs 群聊沉默纪律），
并决定 `step_3` 是否在模型没调表达工具时替 agent 投递回复。详见
`channel_prompts.py.md` / `step_3_agent_loop.py.md` 同日条目。`is_dm` 判错的代价
从「显示不准」升级成「agent 对真人的私聊装死」。

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
