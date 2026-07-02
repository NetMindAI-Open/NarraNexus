---
code_file: src/xyz_agent_context/module/narramessenger_module/
stub: false
last_verified: 2026-06-18
---

## Why it exists

NarraMessenger (formerly **NexusMatrix**) is NetMind's Matrix-based IM
platform. This package integrates it as an IM channel on the generic
`channel_*_base` abstraction — the same shape as lark/slack/telegram.

A predecessor `matrix_module` existed historically (real Matrix client,
adaptive polling) and was removed in the channel-abstraction refactor
(`9322050b`, 2026-04-15). This package is the re-introduction, but with a
deliberately simpler transport.

## Design decisions

- **Transport = Gateway Polling + `/chat/send`, pure HTTP bearer-only. No
  Matrix client.** Empirically (2026-06-17 probes) the NarraMessenger gateway
  pre-filters DM/@mention AND authorizes before handing us an invocation, and
  `/chat/send` delivers replies/proactive messages with no 15-min deadline.
  So we get the whole loop over plain HTTP and the base pipeline is used
  **unchanged** (no per-event authorize step, no base-class edit).
- **Text-only for the agent.** Non-text (images/files/audio) arrive flattened
  as `[Image]` / `[File: x]` placeholders inside the message string — there is
  no attachment payload, so no `fetch_attachments`.
- **v1 reply policy = `narra_send` → `/chat/send` only** (not `/reply`). The
  15-min/error-indicator boundary of leaving the invocation un-`/reply`'d is a
  known open item to test; switching is a one-line change in the send path.

## Files

- `narramessenger_trigger.py` — gateway long-poll trigger (`ChannelTriggerBase`)
- `narramessenger_module.py` — channel module (`ChannelModuleBase`) + sender + instructions
- `narramessenger_context_builder.py` — prompt assembly from the invocation payload
- `_narramessenger_client.py` — aiohttp client for the gateway + chat-send endpoints
- `_narramessenger_credential_manager.py` — CRUD for `channel_narramessenger_credentials`
- `_narramessenger_mcp_tools.py` — `narra_send` / `narra_status` MCP tools
- `run_narramessenger_trigger.py` — standalone trigger entry point

MCP port: **7833** (after telegram 7832).
