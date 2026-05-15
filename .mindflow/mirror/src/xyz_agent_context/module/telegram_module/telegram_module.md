---
code_file: src/xyz_agent_context/module/telegram_module/telegram_module.py
stub: false
last_verified: 2026-05-10
---

## Why it exists

Phase 4 of the IM channel abstraction (see
``reference/self_notebook/specs/2026-05-08-im-integration-design.md`` § 8).
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
  See ``reference/self_notebook/todo/2026-05-09-slack-channel-reply-policy.md``
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
- **Iron rule 6: text-only ingress (Phase 4).** ``parse_event`` drops
  photos/voice/files/stickers silently. Outbound media still works via
  ``tg_cli`` (no curated skill md, but the methods work) — the rule
  scopes the *receive* side, not the *send* side.
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
  ``hook_data_gathering`` template, MCP server creation glue).
- **Downstream**:
  - ``TelegramCredentialManager`` — credential CRUD with getMe + getChat.
  - ``register_telegram_mcp_tools`` — 5 MCP tools on the FastMCP server.
  - ``TelegramSDKClient`` — raw aiohttp Bot API wrapper.
  - ``WorkingSource.TELEGRAM`` — enum entry that ties Telegram-triggered
    events back through the ``hook_after_event_execution`` filter.

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
