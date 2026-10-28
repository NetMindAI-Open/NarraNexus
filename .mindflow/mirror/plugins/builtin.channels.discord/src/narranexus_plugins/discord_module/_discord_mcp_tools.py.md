---
code_file: plugins/builtin.channels.discord/src/narranexus_plugins/discord_module/_discord_mcp_tools.py
stub: false
last_verified: 2026-08-11
---
## 2026-08-11 (PR-H) — 写侧(bind/unbind/status)迁入 seam

bind/status/unbind 不再本地 `_get_manager`（已删）——bind→`seam.bind`（经各自 do_bind + owner-gated `/api/<ch>/bind` 路由）、unbind→`seam.unbind`、status 的 live-check→`seam.test_connection`（POST /test）。**tool 文件 get_mcp_db_client == 0，写路径不再需本地 db**。


## 2026-08-11 (PR-A) — 读凭据改走 ChannelCredentialStore seam

`_get_credential` 不再 `get_mcp_db_client()`+manager 直连库，改 `get_channel_credential_store().get_credential("discord", …)`
→ `_cred_from_raw` 重建 dataclass，send/reply/read_history 里的 `cred.bot_token` 用法零变化；本地 DirectStore、
云端 HttpStore 打 owner-gated 端点。`_get_agent_name` 同理走 seam。**已知留尾**：bind/status/unbind 是写/生命周期，
读-only Protocol 未覆盖，`_get_manager` 暂用 `ChannelDirectStore().get_manager("discord")`（本地专用、无 HttpStore 对应），
故云端写工具仍需本地 db——写路径迁移前 mcp 摘不掉 DB_PASSWORD（见 [[channel_store]] 已知缺口）。

## 2026-07-24 — setup residency (B++): zero-arg `discord_bind` returns the guide

`discord_bind` called with empty credential args now returns `{"success": True,
"setup_guide": _NO_BOT_INSTRUCTION}` instead of a "required" error — the full
walkthrough left the system prompt ([[discord_module]] unbound one-liner) and
is served here on demand. The walkthrough text stays in its original constant;
lazy import avoids a module-import cycle.

## 2026-07-10 — PR #87 review: react tool body → shared helper

`react_to_user_message` now delegates to [[channel_reactions]] `best_effort_react`
(resolve semantic→token, call the SDK, best-effort envelope + log the failure);
only the per-platform `_DISCORD_REACTIONS` map stays here.

## 2026-07-10 — react_to_user_message tool (agent-driven early feedback)

New `react_to_user_message(agent_id, room_id, message_id, emoji)` — shared
semantic `emoji` mapped via `_DISCORD_REACTIONS` to unicode, backed by
`DiscordSDKClient.add_reaction`. Best-effort envelope, never raises.

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
