---
code_file: src/xyz_agent_context/module/discord_module/_discord_mcp_tools.py
stub: false
last_verified: 2026-06-24
---

## Why it exists

Registers the 8 Discord MCP tools on the module's FastMCP server:
``discord_send`` / ``discord_reply`` / ``discord_read_history`` /
``discord_dm`` / ``discord_list_channels`` + ``discord_bind`` /
``discord_status`` / ``discord_unbind``.

``discord_dm(user_id, text)`` covers proactive DMs to a user who hasn't
messaged the bot (opens the DM channel via ``POST /users/@me/channels``
then sends — replying inside an existing DM just uses ``discord_send``
with the inbound channel id). ``discord_list_channels`` lets the agent
discover a ``channel_id`` for a specific channel (filtered to postable
types 0/5) when it didn't get one from an inbound message — the answer to
"users don't know where to find the channel id".

## Design decisions

- **Messaging-first, no generic dispatcher.** Deliberately NOT a
  ``discord_cli`` passthrough and NO ``discord_skill`` doc loader (the
  Slack/Telegram pattern). Dedicated send/reply/read tools keep the
  agent-facing surface small and the main "reply" path unambiguous.
- **Multi-tenant, demux on ``agent_id``.** Like the other channels, the
  dev MCP server serves all agents; each tool re-loads the credential
  for the passed ``agent_id`` (caller agent_id is not verified at this
  layer — same posture as ``register_slack_mcp_tools``).
- **``discord_reply`` vs ``discord_send``.** Reply references the inbound
  message id (inline arrow); send is a plain post. Both auto-split at
  2000 chars via ``DiscordSDKClient``.

## Upstream / downstream

- **Upstream**: ``XYZBaseModule.get_mcp_db_client``,
  ``DiscordCredentialManager``, ``_discord_service`` (bind/test),
  ``DiscordSDKClient`` (send/reply/history).
- **Downstream**: invoked by the agent loop; ``discord_send`` /
  ``discord_reply`` ``text`` args are what ``_extract_discord_reply``
  scrapes for inbox display.

## Gotchas

- Tool names are load-bearing for reply extraction — see
  ``discord_module.md`` and ``discord_trigger.md``. Rename in lockstep.
- The arg guards reject **whitespace-only** ``text`` (``not text.strip()``),
  not just empty — a degenerate "   "/"\n" reply otherwise posts a
  blank-looking Discord message. ``discord_sdk_client`` skips whitespace
  chunks too (the last-line choke point); both layers must keep the guard.
