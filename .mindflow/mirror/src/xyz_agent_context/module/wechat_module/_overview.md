---
code_file: src/xyz_agent_context/module/wechat_module/__init__.py
stub: false
last_verified: 2026-06-24
---

# wechat_module/ — personal-WeChat (iLink) channel

## Why it exists

A full personal-WeChat channel integration built on the shared
``ChannelModuleBase`` / ``ChannelTriggerBase`` framework — the same
spine that backs Lark, Slack, and Telegram. The package's
``__init__.py`` re-exports ``WeChatModule`` so the module registry
(``module/__init__.py`` ``MODULE_MAP``) can pick it up like any other
channel.

The distinguishing trait vs. the bot channels: this connects a
**personal** WeChat account through the iLink HTTP gateway
(``host https://ilinkai.weixin.qq.com``), authenticated by scanning a
login QR — there is no bot token to paste, and inbound delivery is
**PULL-only long-poll**, not a webhook.

## Files in this package

- ``wechat_sdk_client`` — thin HTTP client for the iLink gateway:
  ``fetch_qrcode`` / ``poll_qrcode_status`` (bind-time) plus the
  message pull / send calls. The one place that knows the gateway's
  wire format.
- ``_wechat_credential_manager`` — persists / sanitises the per-agent
  binding (``channel_wechat_credentials``). ``bind`` stores the iLink
  ``bot_token`` + ``base_url``; ``get_public`` returns a view with NO
  token. Private (``_`` prefix) — only the route and trigger touch it.
- ``wechat_context_builder`` — assembles the per-message ContextData
  the runtime needs (sender, owner trust signal, conversation
  threading) so WeChat DMs flow through the standard pipeline.
- ``_wechat_mcp_tools`` — the agent-facing WeChat tools (send message,
  etc.), served on **MCP port 7835**. Private impl behind the MCP
  server.
- ``wechat_trigger`` — ``WeChatTrigger`` (subclass of the channel
  trigger base): long-polls the gateway per bound credential, dedups
  via ``channel_seen_messages``, and drives each inbound message into
  the runtime.
- ``wechat_module`` — ``WeChatModule`` (subclass of the channel module
  base): the registered Module, ``get_config()``, wiring the trigger /
  context builder / MCP tools together.
- ``run_wechat_trigger`` — standalone process entry point that boots
  ``WeChatTrigger`` outside FastAPI (see its own mirror).

## Collaboration with sibling directories

Mirrors the Telegram/Lark/Slack channel layout: a Module + Trigger +
ChannelSender(MCP tools) triad over the shared channel base classes.
The bind REST surface lives in ``backend/routes/wechat.py``; the
frontend binding card is
``frontend/src/components/awareness/WeChatConfig.tsx`` (registered in
``IMChannelsSection``'s ``IM_CHANNELS``).

## Gotchas

- Binding rule #3 — modules are independent; this package must not
  import from another channel module. Shared behaviour lives in the
  channel base classes, not cross-module imports.
- The owner's wxid is opaque at bind time (the gateway only reveals it
  on a real inbound DM), so the owner trust signal activates on first
  contact — reflected in both the credential manager and the frontend
  "owner pending" state.
