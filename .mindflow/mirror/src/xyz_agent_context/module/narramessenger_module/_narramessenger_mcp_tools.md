---
code_file: src/xyz_agent_context/module/narramessenger_module/_narramessenger_mcp_tools.py
stub: false
last_verified: 2026-06-18
---

## Why it exists

The agent-callable MCP tools for NarraMessenger. Registered on the module's
FastMCP server (port 7833) via `register_narramessenger_mcp_tools`.

## Tools

- **`narra_reply(agent_id, invocation_id, text)`** — the REPLY path for an
  inbound message. Calls `/invocations/{id}/reply`, which delivers AND closes
  the invocation (so the platform does NOT fire the 15-min timeout). The
  `invocation_id` comes from the turn's identity block (threaded by
  `build_extra_data` from the poll payload).
- **`narra_send(agent_id, room_id, text)`** — PROACTIVE send via `/chat/send`
  (no invocation to close, no time limit). For agent-initiated messages (Jobs,
  follow-ups) — NOT for replying to a message you were invoked on.
- **`narra_bind(agent_id, bind_command)`** — bind from a pasted bind link;
  delegates to `_narramessenger_service.do_bind` (Gateway bind + write row).
- **`narra_status(agent_id)`** — sanitised binding view (no token) + live
  `/status` check.

## Design decisions

- Credential is fetched via `XYZBaseModule.get_mcp_db_client()` (the dev MCP
  server is multi-tenant; agent_id is a tool argument, not verified here —
  same model as telegram/slack).
- **reply vs send split (2026-06-18)**: replies MUST close the invocation, so
  they go through `narra_reply`→`/reply`; proactive messages have no invocation
  and go through `narra_send`→`/chat/send`. Using `/chat/send` for a reply was
  the cause of the 15-min "agent did not reply" timeout.

## Gotchas

- Tool names are load-bearing: the trigger's `extract_output` and the
  MessageSourceRegistry handler both match on `"narra_reply"` / `"narra_send"`.
  Renaming requires updating both.
