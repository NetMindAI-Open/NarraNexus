---
code_file: plugins/builtin.channels.wechat/src/narranexus_plugins/wechat_module/_wechat_mcp_tools.py
stub: false
last_verified: 2026-08-21
---

## 2026-08-21 — `wechat_send` docstring 说明 `context_token` 仅 direct 路径需要（PR-2 增量审 Minor）

补 deployment note:managed 路径只按 `to_user_id` 投递、`context_token` 不用(可空),只有 direct-iLink 回退才需要 inbound 的 `context_token`。即 managed 部署**能**向已知 `to_user_id` 主动发起,direct-iLink 是本轮活跃会话内 reply-only。让约束写在真正要调用它的地方(配合社交模块 §3b surface-blind 的「有些渠道只能在活跃会话内投递」)。纯文档。
## 2026-08-11 (PR-H) — unbind 迁入 seam

wechat_unbind→`seam.unbind("wechat")`，删本地 `_get_manager`。无 bind 工具(扫码后端专属)。tool 文件 get_mcp_db_client==0。

## 2026-08-11 (PR-B..D) — 读凭据改走 ChannelCredentialStore seam

`_get_credential` 不再 `get_mcp_db_client()`+manager 直连库，改 `get_channel_credential_store().get_credential("wechat", …)` → `_cred_from_raw` 重建 dataclass，工具里 cred.bot_token / cred.to_public_dict() 用法零变化；本地 DirectStore、云端 HttpStore 打 owner-gated 端点。**写留尾**：bind/status/unbind 经 `_get_manager`→`ChannelDirectStore().get_manager("wechat")`（本地专用、无 HttpStore 对应），故云端写工具仍需本地 db——写路径迁移前 mcp 摘不掉 DB_PASSWORD（见 [[channel_store]] 已知缺口）。


## 2026-08-10 — wechat_send 改走 wechat_outbound 路由

发送后端从 `send_text_once` 换成 [[wechat_outbound.py]] 的
`send_wechat_text`。**工具签名/描述一字未动**(`to_user_id` /
`context_token` 参数保留)——managed 路由下 token 被忽略(平台按
room 解析收件人),direct 路由语义不变;prompt 面零变化是平台
第二轮回复对切换方式的明确要求(改造面收敛在工具后端一处)。

## 2026-07-10 — react_to_user_message tool (uniform, but unsupported)

Added `react_to_user_message` so the capability is uniform across IM channels,
but iLink v1 has no reaction API — it always returns the unsupported envelope
(`{success:false, reason}`) and the instruction tells the agent to send a short
`wechat_send` message instead.

## Why it exists

Registers the agent-callable WeChat (iLink) MCP tools on the FastMCP
server created by ``ChannelModuleBase.create_mcp_server``. This is the
agent's **reply path** — iLink is PULL-only, so the trigger only
*ingests*; sending a DM back goes through a tool call, not the trigger.
The trigger hands the agent the inbound ``to_user_id`` + ``context_token``
in the prompt; the agent calls ``wechat_send`` to answer.

Three tools, named ``wechat_*`` to mirror Telegram's ``tg_*`` /
Slack's ``slack_*`` so the agent's "interact with channel X" surface
stays uniform across IM channels:

- ``wechat_send(agent_id, to_user_id, context_token, text)`` — send a
  DM reply (the canonical reply, analogous to Telegram's ``tg_cli``
  ``method=sendMessage``)
- ``wechat_status(agent_id)`` — binding status, no raw token
- ``wechat_unbind(agent_id)`` — remove the binding

## Design decisions

- **There is intentionally NO ``wechat_bind`` tool.** This is the
  load-bearing difference from Telegram's tool set. Telegram binds by
  the agent/user pasting a Bot Token, so ``tg_bind`` makes sense.
  WeChat binds via a **QR-scan flow** driven by the Brain/Channels
  panel UI + ``backend/routes/channels/wechat.py`` — the user scans a login QR
  in their WeChat app. That handshake cannot be performed by the agent
  through a tool call, so exposing a ``wechat_bind`` tool would only
  invite the agent to attempt something it can't do. Binding is
  deliberately kept off the agent surface.
- **No ``wechat_cli`` + ``wechat_skill`` dispatcher pair.** Telegram
  ships a generic Bot API dispatcher (``tg_cli``) plus a doc lookup
  (``tg_skill``) because the Bot API has ~100 methods worth surfacing.
  iLink's agent-facing surface is just "send a DM" — one verb — so a
  single typed ``wechat_send`` is the whole reply path. No generic
  dispatcher, no skill loader.
- **``wechat_send`` validates then delegates to ``send_text_once``.**
  Empty/whitespace text → ``{"ok": False, "error": "empty_text"}``;
  missing ``to_user_id`` → ``missing_to_user_id``; no bound credential
  → ``no_credential`` with a hint pointing at the Channels panel. The
  actual HTTP send is ``wechat_sdk_client.send_text_once`` (resolves
  the SDK ``sendmessage`` against ``cred.bot_token`` / ``cred.base_url``).
- **Returns a flat ``{"ok": bool, "error"?}`` envelope, never raises.**
  The agent reads ``ok`` directly. ``wechat_status`` / ``wechat_unbind``
  use the ``{"success": ..., "data": ...}`` shape instead — matching
  the credential-management convention rather than the send convention.
- **Token never appears in tool output.** ``wechat_status`` returns
  ``credential.to_public_dict()`` (raw ``bot_token`` excluded by
  construction) and tacks on ``"bound": True``. The unbound case
  returns ``{"bound": False, "data": None}`` so the agent can tell
  "no account" from "account but no detail".
- **MCP DB client lookup via ``XYZBaseModule.get_mcp_db_client``.**
  Tools run inside the MCP server process, which doesn't carry the
  agent's database client by reference; ``_get_manager`` resolves a
  fresh ``WeChatCredentialManager`` per call. Same resolver pattern as
  all other channel MCP tool modules.

## Upstream / downstream

- **Registered by**: ``WeChatModule.register_mcp_tools`` →
  ``register_wechat_mcp_tools`` → ``ChannelModuleBase.create_mcp_server``.
- **Calls**: ``WeChatCredentialManager`` (``get`` / ``unbind``),
  ``wechat_sdk_client.send_text_once``.
- **Exposed on**: MCP port 7835 (``WECHAT_MCP_PORT``). Channel-port
  range: Lark=7830, Slack=7831, Telegram=7832, NarraMessenger=7833,
  Discord=7834, WeChat=7835.
- **Consumed by**: the trigger's ``extract_output`` scrapes the
  ``wechat_send`` call's ``text`` arg out of the turn for the inbox
  record — so this tool's argument shape is part of the inbox contract,
  not just the send contract.

## Gotchas

- ``wechat_send`` needs BOTH ``to_user_id`` and ``context_token``. The
  ``context_token`` is iLink's conversation addressing handle and only
  exists on the inbound message — the agent must pass through the
  values from the message context verbatim, it cannot synthesize them.
- Adding a new tool here requires also advertising it in the module's
  ``contribute_instructions`` template — the agent only knows what the prompt
  tells it. The ``MessageSourceRegistry`` reply-tool-name tuple in
  ``wechat_module.py`` must also be kept in sync, or a new reply tool's
  output gets logged as "Background activity" instead of a real reply.
- ``send_text_once`` can return ``False`` on an app-level ``ret != 0``
  (e.g. expired iLink session) even though the HTTP request was 200 —
  that surfaces here as ``{"ok": False, "error": "send_failed"}``. A
  failed send does not retry inside the tool.
