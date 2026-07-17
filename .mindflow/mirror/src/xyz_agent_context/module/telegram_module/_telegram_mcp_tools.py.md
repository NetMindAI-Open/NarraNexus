---
code_file: src/xyz_agent_context/module/telegram_module/_telegram_mcp_tools.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — PR #87 review: react tool body → shared helper

`react_to_user_message` now delegates to [[channel_reactions]] `best_effort_react`;
a small `_react` wrapper turns `set_message_reaction`'s `False` return into a raise
so the shared helper reports it. Only `_TELEGRAM_REACTIONS` stays here (`done`
remapped 🎉→👌 so it's distinguishable from `celebrate`).

## 2026-07-10 — react_to_user_message tool (agent-driven early feedback)

New `react_to_user_message(agent_id, room_id, message_id, emoji)` — shared
semantic `emoji` mapped via `_TELEGRAM_REACTIONS` to Telegram's allowed set
(✅ is NOT allowed → `done`=🎉), backed by `TelegramSDKClient.set_message_reaction`;
closes the client in `finally`. Best-effort envelope, never raises.

## Why it exists

Registers the agent-callable Telegram MCP tools on the FastMCP server
created by ``ChannelModuleBase.create_mcp_server``. Five tools, named
to mirror Slack's ``slack_*`` and Lark's ``lark_*`` so the agent's
"interact with channel X" surface stays uniform across IM channels:

- ``tg_cli`` — generic Bot API dispatcher backed by
  ``TelegramSDKClient.api_call``
- ``tg_skill`` — fetch hand-curated method docs
- ``tg_bind`` / ``tg_status`` / ``tg_unbind`` — credential management

## Design decisions

- **``tg_cli`` + ``tg_skill`` is the canonical pattern.** Lark and
  Slack ship the same pair (one dispatcher + one doc lookup). This
  keeps ~80 Bot API methods reachable without baking each into the
  MCP surface, while ``tg_skill`` keeps the system prompt small.
- **Method-name regex is camelCase WITHOUT dots.** Different from
  Slack's ``chat.postMessage`` which has dots. Telegram methods are
  all camelCase single tokens (``sendMessage``, ``getUpdates``,
  ``editMessageText``, ``setMessageReaction``). The regex
  ``^[a-z][a-zA-Z0-9]+$`` rejects path-traversal-shaped inputs.
- **``tg_cli`` returns Telegram's native envelope, never raises.**
  Agents read ``{"ok": bool, "result"?, "error"?}`` directly. This
  matches the ``TelegramSDKClient.api_call`` semantics. Hot-path
  Python callers use the wrappers in ``TelegramSDKClient`` instead.
- **Non-curated methods log INFO but execute.** Telegram has ~100
  methods; we curate ~25. A new method (``sendPaidMedia``) or one
  we skipped (``sendPhoto``) still works through ``tg_cli`` — the
  log line lets us notice the gap without blocking the agent.
- **Privacy-mode reminder embedded in ``tg_cli`` docstring.** The
  agent occasionally hits "bot can't see group messages" via tool
  calls; the docstring redirects to BotFather's ``/setprivacy``
  setting since that's a setup gotcha the agent itself can't fix.
- **Token never appears in tool output.** ``tg_status`` returns
  ``credential.to_public_dict()``, which excludes the raw token by
  construction. ``tg_bind`` errors quote the error description but
  not the token even on validation failure.
- **``do_bind`` / ``do_test_connection`` shared with REST.** Single
  ``_telegram_service.py`` so MCP and HTTP paths stay identical.
- **MCP DB client lookup via ``XYZBaseModule.get_mcp_db_client``.**
  Tools run inside the MCP server process which doesn't carry the
  agent's database client by reference; this resolver pattern is
  shared across all MCP tool modules.

## Upstream / downstream

- **Registered by**: ``TelegramModule.register_mcp_tools`` →
  ``ChannelModuleBase.create_mcp_server``.
- **Calls**: ``TelegramCredentialManager``, ``TelegramSDKClient``,
  ``_telegram_service.do_bind``, ``_telegram_skill_loader.get_skill_loader``.
- **Exposed on**: MCP port 7832 (``TELEGRAM_MCP_PORT``).

## Gotchas

- Adding a new MCP tool here requires also referencing it in the
  module's ``get_instructions`` template — agents only know what the
  prompt advertises.
- ``tg_skill`` ignores ``agent_id`` (skill docs are global) — the
  parameter is kept for symmetry with ``lark_skill`` / ``slack_skill``
  signatures.
- Method regex would reject hypothetical Telegram methods with an
  underscore (none currently exist). If Bot API ever introduces one,
  the regex needs widening.
- ``tg_cli`` opens + closes a fresh ``TelegramSDKClient`` per call.
  High-frequency tool use will TCP-handshake every invocation; if
  hot-path RPS becomes a concern, add an LRU client cache here.
