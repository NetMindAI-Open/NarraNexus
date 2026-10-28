---
code_file: plugins/builtin.channels.telegram/src/narranexus_plugins/telegram_module/telegram_context_builder.py
stub: false
last_verified: 2026-08-19
---

## 2026-08-19 — 部署窗口回落读（存量记忆不丢）

`get_conversation_history` 在新表 `inbox_thread_messages` 查空时，回落读一次旧 `bus_messages`（`channel_id=telegram_{chat_id}`）。**按 agent 隔离**：只认本 bot 的回复（`from_agent==agent_id`）与用户消息（`telegram_user_*`），另一个 bot 在同一共享 chat 的回复被排除。与 `wipe_service` 对齐（telegram DM 是单成员 channel，wipe 会删这些行）。回填 runbook 跑完后删除。没有它，部署当天每个存量 telegram bot 会失忆。

## 2026-08-17 — 历史改读 inbox 记录

Telegram Bot API 没有 history 端点，所以历史一直来自本地记录。那份记录 2026-08-17 从
`bus_messages` 搬到了 `inbox_thread_messages`，**这个读取方必须跟着搬**——留在原地
Telegram 会失忆（2026-05-13 那个「再试一下」被理解成重试渠道测试的 bug 会复发）。

判别「这行是谁说的」从比对 `from_agent == agent_id` 改成读 `direction` 列：记录层直接
陈述方向，不再把它编码进一个合成发送者 id。

**这是 inbox 记录同时是 operational 的两处之一**（另一处是 wechat）——对没有历史 API 的
渠道，它就是 agent 的对话记忆，不只是给人看的。见 [[inbox_recorder]]。

## 2026-08-06 — `room_type` 现在是行为开关

判定逻辑没变（`chat_id` 以 `-` 开头 = 群），只是换用 `channel_prompts` 的
`ROOM_TYPE_*` 常量。**语义变了**：这个值现在**选择注入哪份通讯协议**（私聊
「默认回复」vs 群聊沉默纪律），并决定 `step_3` 是否在模型没调表达工具时替 agent
投递回复。详见 `channel_prompts.py.md` / `step_3_agent_loop.py.md` 同日条目。

因此下面「`room_type` 靠 chat_id 符号推断」那条注意事项的赌注变大了：推断错
不再只是 prompt 里一行显示不准，而是把私聊当群聊对待（agent 对真人装死）或反过来。

## Why it exists

Telegram-side implementation of ``ChannelContextBuilderBase``. Builds
the per-turn context the agent sees when a Telegram message lands:
sender identity, room metadata, the inbound message body, and the
exact ``tg_cli`` invocation shape it should use to reply.

## Design decisions

- **``get_conversation_history`` returns ``[]`` unconditionally.**
  Telegram Bot API has NO equivalent of Slack's ``conversations.history``
  or lark-cli's ``+messages-list`` — bots only see messages that arrive
  AFTER they're added to a chat (or after they were DM'd). Pretending
  otherwise would force a synthetic stub and risk hallucinated history.
  The agent gets multi-turn context exclusively from ``ChatModule``'s
  per-agent memory, which is independent of channel.
- **``get_room_members`` returns ``[]``.** Bots can call
  ``getChatAdministrators`` / ``getChatMemberCount`` but cannot
  enumerate non-admin members. We already infer "is this a group?"
  from the sign of ``chat_id`` (negative = group, positive = DM), so
  the empty list costs us nothing in Phase 4.
- **``room_type`` derived from ``chat_id`` sign, not chat metadata.**
  ``chat_id.startswith("-")`` is the cheap public signal. No extra API
  call.
- **``reply_instruction`` hand-builds the ``tg_cli`` invocation
  shape.** Pre-formatted with the actual ``chat_id`` /
  ``message_thread_id`` so the agent doesn't have to re-derive them
  from context. Includes the plain-text warning inline so the rule
  about ``parse_mode`` reaches the agent at the call site, not just in
  the system prompt.
- **``send_tool_name = "tg_cli"``.** The ``ChannelContextBuilderBase``
  contract surfaces this so the templated ``message_info`` can
  reference the canonical send tool by name. Slack uses
  ``slack_send``; Lark uses ``lark_cli``.
- **``room_name`` left empty.** ``chat.title`` is in the raw update but
  ``parse_event`` doesn't currently propagate it, and the renderer
  treats empty string as "use room_id". Wiring this up is a follow-up.
- **Holds raw ``ParsedMessage`` + ``TelegramCredential``.** No copy /
  transform of fields — we lazily read on each accessor call so any
  future ParsedMessage extensions appear without touching this file.

## Upstream / downstream

- **Upstream**: ``ChannelContextBuilderBase``.
- **Constructed by**: ``TelegramTrigger.create_context_builder``.
- **Reads**: ``ParsedMessage``, ``TelegramCredential``.

## Gotchas

- Returning fake conversation history here would silently leak across
  to ``ChatModule`` memory; the empty list is load-bearing.
- ``room_type`` heuristic breaks if Telegram ever ships a chat type
  with positive id and group semantics — none exists today.
- ``reply_instruction`` is duplicated content vs. the system prompt's
  "When replying" section. Drift will confuse the agent — keep them in
  sync (or refactor to a single source).
