---
code_file: plugins/builtin.channels.telegram/src/narranexus_plugins/telegram_module/telegram_module.py
stub: false
last_verified: 2026-09-13
---

## 2026-09-13 — marker 的 path 是 JSON 字符串

给 agent 的附件说明改为：`path=` 的值是 JSON 字符串。普通路径就是引号里的文字；含转义（`\"`、`\\`、`\t`、`\n`）时要先解码再交给 Read。起因是 `file_marker` 改用无损的 `exact_literal` 编码 path，照字面读带转义的路径会读错文件。

## 2026-09-13（PR#401）— 指令里的附件 marker 形状同步为真实形状

marker 形状现在是 `[User uploaded file: name="…", path="…", mime="…", kind="…"[, transcript="…"] — use Read tool to view]`（`attachment_schema.file_marker`：每个值都是 JSON 字面量，kind 从 head 挪成字段），说明文字跟着改，并提示把 `path` 引号内的文本交给 Read。

## 2026-09-07 — 删掉 import 期的 MessageSourceRegistry 注册

顶层那段 `try: MessageSourceRegistry.register(...) except ValueError: pass` 已删；
`_extract_telegram_reply` 原样保留，现在由 `descriptor.py` 的 `reply_extractor_ref` 按名字指
过来、首次抽取时才解析。

原注释声称「重复注册会 hard error，这是正确的 loud-fail」，而三行之下的 `pass` 恰好把它
抵消了——两个插件抢同一个 source 名不会报错，只是「谁先 import 谁赢」。现在名字冲突由
`Registry.register` 抛 `RegistryConflict`，真的响。

行为面的净效果：`tg_cli` 这类回复工具是否被识别，不再取决于本进程有没有 import 过这个模
块，而取决于本发行版是否装了这个渠道。

## 2026-08-19 — owner 工具判定改用 is_owner_tool

`_extract_telegram_reply` 的「是不是回 owner」从硬写 `"notify_owner" in tool_name` 改为 `is_owner_tool(tool_name)`（认 `reply_owner`/`notify_owner`，裸名或 MCP 前缀）。与 step_3 同一反模式的修复：硬写单名在 register 规则再动时必然半更新。narramessenger 故意不认 owner 工具，不动。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`reply_tool_names = ("tg_cli",)`(NexusPower 投递面声明;门控随 is_bound)。

## 2026-07-24 — setup residency (B++): unbound → one-liner + tool suppression

Declares `all_tool_names` + `setup_tool_names = {tg_bind}` per the
[[channel_module_base]] setup-residency contract. The `contribute_instructions`
unbound branch now returns `unbound_setup_line()` instead of the full
onboarding walkthrough (bound-but-info-missing returns ""); the walkthrough is
served on demand by zero-arg `tg_bind` (see [[_telegram_mcp_tools]]). While
unbound, every non-setup tool's schema is stripped from the model context.

## 2026-07-10 — early-feedback removed from contribute_instructions (moved to trigger)

The "ack early" block is gone from `contribute_instructions`; it's now injected per-turn
by the trigger (`_early_feedback_prefix`, see [[channel_trigger_base]]).

## 2026-07-10 — PR #87 review: early-feedback via shared render

The Telegram early-feedback section is now produced by [[channel_reactions]]
`render_early_feedback(tool_ref="react_to_user_message", …)` instead of an inline
hardcoded string.

## 2026-07-10 — contribute_instructions surfaces early-feedback affordance

Operational prompt now includes an "Early feedback" block (when in the Telegram
channel with a `source_message_id`): a generic SHOULD directive — for non-trivial
requests, ACK FIRST (react `on_it` via `react_to_user_message` with real
chat/message id embedded, or a quick "on it") THEN do the work. Generic
interaction rule in the system prompt, not per-agent Awareness (rule #4); not a
hard guarantee (rule #15).

## 2026-07-03 — handler registers `dedicated_trigger=True`

MessageBusTrigger derives its do-not-redispatch channel prefixes from this
flag (see message_source_handler.py.md, 2026-07-03).

## Why it exists

Phase 4 of the IM channel abstraction (2026-05-08 design, author-local).
Telegram's ``ChannelModuleBase`` subclass — the third application of the
Phase 1+2 surface and the deliberate "simplest" channel: one Bot Token
from @BotFather, no OAuth, no admin approval, no manifest YAML, no
Socket Mode.

The architectural value is the contrast: this file is small precisely
because the abstraction earns its keep on a channel without any of the
multi-tenant ceremony Lark and Slack carry. If a new IM channel needs
substantially more code than this, the channel really is more complex,
not the abstraction failing.

## Design decisions

- **Two prompt modes only.** ``_NO_BOT_INSTRUCTION`` (no credential
  bound — drives the @BotFather walkthrough) and the operational
  template (when bound). Same shape as Slack.
- **``_NO_BOT_INSTRUCTION`` Step 2 explicitly tells users to KEEP
  privacy mode on (the default).** Earlier drafts of this module (and
  Phase 4 plan) mistakenly recommended ``/setprivacy → Disable``,
  reasoning that "the bot would be deaf in groups otherwise." But
  "deaf" was the wrong frame: privacy mode default-on means the bot
  only sees ``/commands`` and @-mentions in groups, which is the
  CORRECT behaviour — same @-mention-only group UX Slack is still
  trying to retrofit (Phase 5 todo). Disabling privacy floods the
  agent with every group message, wastes tokens, and risks spam-
  replies. The instruction now says the opposite of the original draft
  ("DO NOT disable privacy unless..."). Iron rule 1 also enforces
  agent-side: "in groups/supergroups you reply ONLY when @-mentioned".
  for the cross-channel symmetry argument.
- **No Slack-style App Manifest.** Replaced by the BotFather chat
  sequence. There is nothing to paste into a portal — every step
  happens inside Telegram itself.
- **Owner identity via @username, not email.** Telegram has no email
  surface for users; ``getChat("@handle")`` resolves the immutable
  numeric user_id. ``owner_username`` is OPTIONAL — without it the
  trust signal stays off and every Telegram sender is treated as
  untrusted (documented in ``trust_block``). This is intentional:
  groups full of strangers must not be able to spoof owner-ship by
  guessing the handle.
- **Iron rule 3: plain text only (no parse_mode).** Telegram
  MarkdownV2's escape rules (``_*[]()~>#+-=|{}.!\``) are aggressive;
  one missed escape returns 400 Bad Request and the agent looks
  broken. Phase 4 stays plain-text; opting into MarkdownV2 is a
  future call.
- **Iron rule 7: inbound attachments SUPPORTED (Phase 1a).** Updated
  from the original "text-only" rule. ``parse_event`` extracts
  documents / photos / voice / audio / video into
  ``raw["attachment_refs"]``; ``fetch_attachments`` downloads bytes
  via ``download_file`` and persists them through ``_persist_attachment``
  on the base. The instruction text now explains the
  ``[User uploaded <kind>: ...path=... transcript=...]`` marker shape (superseded — current shape in the 2026-09-13 entry)
  so the agent uses the built-in ``Read`` tool against the absolute
  path (multimodal — returns PDF / image content blocks natively).
  Stickers / locations / contacts / polls remain ignored. **Keeping
  this rule's text in lockstep with the trigger's ``parse_event``
  coverage matters** — if Phase 2 adds sticker support, this prompt
  must be updated in the same commit or the agent will keep telling
  users they can't send stickers.
- **MCP port 7832.** Continues the channel-port range (Slack=7831,
  Telegram=7832). Picked from the inventory in
  ``module_runner.MODULE_PORTS``.
- **``priority=7``.** After Slack=6, Lark=5. Reordering changes prompt
  section order — keep stable.
- **``send_to_agent`` returns plain dicts, never raises.** Same
  cross-channel sender contract as Slack. ``TelegramSDKError.code``
  carries the upstream description string.
- **``_on_event_executed`` is a no-op stub.** Phase 4 doesn't push
  delivery telemetry. Hook stays declared for future read-receipt /
  reaction-on-success work.

## Upstream / downstream

- **Upstream**: ``ChannelModuleBase`` (Phase 2 base — sender registry,
  ``gather`` template, MCP server creation glue).
- **Downstream**:
  - ``TelegramCredentialManager`` — credential CRUD with getMe + getChat.
  - ``register_telegram_mcp_tools`` — 5 MCP tools on the FastMCP server.
  - ``TelegramSDKClient`` — raw aiohttp Bot API wrapper.
  - ``WorkingSource.TELEGRAM`` — enum entry that ties Telegram-triggered
    events back through the ``after_turn`` filter.

## Gotchas

- The bound-state prompt embeds ``bot_username`` / ``owner_user_id`` /
  ``current_sender_id`` from ``ctx_data.extra_data["telegram_info"]``.
  If ``build_extra_data`` shape ever changes, the f-string renders
  empty without raising — manual eyeball test on rebind.
- ``WorkingSource`` comparison handles both enum and ``str`` form
  (Python 3.11+ ``str(enum)`` quirk); same pattern documented on
  ``ChannelModuleBase``.
- ``_NO_BOT_INSTRUCTION`` Step 2 is **counter-intuitive on first read** —
  it tells users to do nothing (keep default). Future maintainers who
  encounter user reports of "bot doesn't reply in groups" must NOT
  reach for the obvious fix (disable privacy). The right answer is
  "@-mention the bot". Re-introducing the disable recommendation
  silently regresses Phase 4's @-mention-only group behavior into
  noisy-listener mode.
- ``priority=7`` is intentional. Not a free knob.


## 2026-08-18 — owner 工具改名跟随

`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与 `notify_owner`
（未被问就主动告知）。两者行为相同但纪律相反，合成一个工具就要求模型每轮自己判断该用哪种
register。本文件里改到的是该 handler 注册的 `user_reply_tool_names` / 相关文案 —— 一两行，
但 registry 条目是**活的行为**：它决定哪些工具调用算作这个来源的一次回复，也是
`render_origin_declaration` 取 label 的同一条记录。规范解释见
[[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
