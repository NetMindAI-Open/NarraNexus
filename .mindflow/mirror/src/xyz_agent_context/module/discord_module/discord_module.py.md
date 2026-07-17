---
code_file: src/xyz_agent_context/module/discord_module/discord_module.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — early-feedback removed from get_instructions (moved to trigger)

The "ack early" block is gone from `get_instructions`; it's now injected per-turn
by the trigger (`_early_feedback_prefix`, see [[channel_trigger_base]]).

## 2026-07-10 — PR #87 review: early-feedback via shared render

The Discord early-feedback section is now produced by [[channel_reactions]]
`render_early_feedback(tool_ref="react_to_user_message", …)` instead of an inline
hardcoded string.

## 2026-07-10 — get_instructions surfaces early-feedback affordance

Operational prompt now includes an "Early feedback" block (when in the Discord
channel with a `source_message_id`): a generic SHOULD directive — for non-trivial
requests, ACK FIRST (react `on_it` via `react_to_user_message` with real
channel/message id embedded, or a quick "on it") THEN do the work. Generic
interaction rule in the system prompt, not per-agent Awareness (rule #4); not a
hard guarantee (rule #15).

## 2026-07-03 — handler registers `dedicated_trigger=True`

MessageBusTrigger derives its do-not-redispatch channel prefixes from this
flag (see message_source_handler.py.md, 2026-07-03).

## Why it exists

The IM channel abstraction's Discord ``ChannelModuleBase`` subclass —
wires Discord-specific prompt content, the single-token credential
schema, the cross-channel sender, and MCP tool registration onto the
shared mechanism. Sibling of ``slack_module.py`` / ``telegram_module.py``.

## Design decisions

- **Messaging-first surface (no generic CLI / skill loader).** Unlike
  Slack (``slack_cli`` + ``slack_skill``) and Telegram (``tg_cli`` +
  ``tg_skill``), Discord exposes dedicated ``discord_send`` /
  ``discord_reply`` / ``discord_read_history`` tools — no arbitrary REST
  passthrough, no generated API-doc corpus. This was a deliberate scope
  choice (Owner-confirmed) to keep the "receive → reply" main path solid
  and the agent-facing surface small. If a future phase wants full API
  reach, add a ``discord_cli`` dispatcher rather than widening these.
- **Message Content Intent is the one load-bearing manual step.** Both
  ``_NO_BOT_INSTRUCTION`` and ``DiscordConfig.tsx`` call it out: without
  the Portal toggle, Discord delivers events with empty ``content`` and
  the bot "sees" blank messages. The documented diagnostic ("sees
  messages but they're blank → intent off") short-circuits a whole class
  of false trigger/network debugging.
- **Owner trust keys on the numeric user id only.** Discord usernames
  aren't stable identifiers; ``build_extra_data`` compares the inbound
  ``channel_tag.sender_id`` against ``cred.owner_user_id`` (resolved at
  bind from the user-supplied numeric id). No display-name trust ever.
- **MCP port 7834** — next free in the channel range (Lark 7830 / Slack
  7831 / Telegram 7832). Auto-discovered by
  ``module_runner.discover_channel_modules`` via the ``mcp_port`` class
  attr; no manual port-map edit needed.
- **``priority=6``** matches Slack — placed after Lark and the core
  capability modules.
- **``_extract_discord_reply`` registered with ``MessageSourceRegistry``**
  so ChatModule records the agent's real reply text (scraped from the
  ``discord_send`` / ``discord_reply`` ``text`` arg) instead of a
  "Background activity (discord)" placeholder the next turn would filter.

## Upstream / downstream

- **Upstream**: ``ChannelModuleBase`` (sender self-registration,
  ``hook_data_gathering`` template, MCP server glue).
- **Downstream**: ``DiscordCredentialManager`` (CRUD + auth),
  ``register_discord_mcp_tools`` (the 6 tools), ``DiscordSDKClient`` (the
  sender), ``WorkingSource.DISCORD`` (ties messages back through
  ``hook_after_event_execution``).

## Gotchas

- The bound-state prompt embeds ``bot_username`` / ``bot_user_id`` from
  ``ctx_data.extra_data["discord_info"]``; if ``build_extra_data`` shape
  changes the f-string renders empty silently.
- Reply path depends on the MCP tool names containing ``discord_send`` /
  ``discord_reply`` — renaming the tools without updating
  ``_extract_discord_reply`` and ``DiscordTrigger._extract_sent_text``
  breaks inbox reply capture.
