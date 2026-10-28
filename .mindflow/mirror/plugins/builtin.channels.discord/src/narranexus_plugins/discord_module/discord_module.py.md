---
code_file: plugins/builtin.channels.discord/src/narranexus_plugins/discord_module/discord_module.py
stub: false
last_verified: 2026-09-07
---

## 2026-09-07 — 删掉 import 期的 MessageSourceRegistry 注册

顶层那段 `try: MessageSourceRegistry.register(...) except ValueError: pass` 已删；
`_extract_discord_reply` 原样保留，现在由 `descriptor.py` 的 `reply_extractor_ref` 按名字指
过来、首次抽取时才解析。

原注释声称「重复注册会 hard error，这是正确的 loud-fail」，而三行之下的 `pass` 恰好把它
抵消了——两个插件抢同一个 source 名不会报错，只是「谁先 import 谁赢」。现在名字冲突由
`Registry.register` 抛 `RegistryConflict`，真的响。

行为面的净效果：`discord_send` 这类回复工具是否被识别，不再取决于本进程有没有 import 过这个模
块，而取决于本发行版是否装了这个渠道。

## 2026-08-19 — owner 工具判定改用 is_owner_tool

`_extract_discord_reply` 的「是不是回 owner」从硬写 `"notify_owner" in tool_name` 改为 `is_owner_tool(tool_name)`（认 `reply_owner`/`notify_owner`，裸名或 MCP 前缀）。与 step_3 同一反模式的修复：硬写单名在 register 规则再动时必然半更新。narramessenger 故意不认 owner 工具，不动。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`reply_tool_names = ("discord_send", "discord_reply", "discord_dm")`
(NexusPower 投递面声明;门控随 is_bound)。

## 2026-07-24 — setup residency (B++): unbound → one-liner + tool suppression

Declares `all_tool_names` + `setup_tool_names = {discord_bind}` per the
[[channel_module_base]] setup-residency contract. The `contribute_instructions`
unbound branch now returns `unbound_setup_line()` instead of the full
onboarding walkthrough (bound-but-info-missing returns ""); the walkthrough is
served on demand by zero-arg `discord_bind` (see [[_discord_mcp_tools]]). While
unbound, every non-setup tool's schema is stripped from the model context.

## 2026-07-10 — early-feedback removed from contribute_instructions (moved to trigger)

The "ack early" block is gone from `contribute_instructions`; it's now injected per-turn
by the trigger (`_early_feedback_prefix`, see [[channel_trigger_base]]).

## 2026-07-10 — PR #87 review: early-feedback via shared render

The Discord early-feedback section is now produced by [[channel_reactions]]
`render_early_feedback(tool_ref="react_to_user_message", …)` instead of an inline
hardcoded string.

## 2026-07-10 — contribute_instructions surfaces early-feedback affordance

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
  ``gather`` template, MCP server glue).
- **Downstream**: ``DiscordCredentialManager`` (CRUD + auth),
  ``register_discord_mcp_tools`` (the 6 tools), ``DiscordSDKClient`` (the
  sender), ``WorkingSource.DISCORD`` (ties messages back through
  ``after_turn``).

## Gotchas

- The bound-state prompt embeds ``bot_username`` / ``bot_user_id`` from
  ``ctx_data.extra_data["discord_info"]``; if ``build_extra_data`` shape
  changes the f-string renders empty silently.
- Reply path depends on the MCP tool names containing ``discord_send`` /
  ``discord_reply`` — renaming the tools without updating
  ``_extract_discord_reply`` and ``DiscordTrigger._extract_sent_text``
  breaks inbox reply capture.


## 2026-08-18 — owner 工具改名跟随

`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与 `notify_owner`
（未被问就主动告知）。两者行为相同但纪律相反，合成一个工具就要求模型每轮自己判断该用哪种
register。本文件里改到的是该 handler 注册的 `user_reply_tool_names` / 相关文案 —— 一两行，
但 registry 条目是**活的行为**：它决定哪些工具调用算作这个来源的一次回复，也是
`render_origin_declaration` 取 label 的同一条记录。规范解释见
[[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
