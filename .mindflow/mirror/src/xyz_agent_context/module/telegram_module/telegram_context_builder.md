---
code_file: src/xyz_agent_context/module/telegram_module/telegram_context_builder.py
stub: false
last_verified: 2026-05-09
---

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
