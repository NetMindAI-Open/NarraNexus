---
code_file: src/xyz_agent_context/module/slack_module/_slack_mcp_tools.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — PR #87 review: react tool body → shared helper

`react_to_user_message` now delegates to [[channel_reactions]] `best_effort_react`
(resolve semantic→token, call the SDK, best-effort envelope + log the failure);
only the per-platform `_SLACK_REACTIONS` map stays here.

## 2026-07-10 — react_to_user_message tool (agent-driven early feedback)

New `react_to_user_message(agent_id, room_id, message_id, emoji)` — shared
semantic `emoji` mapped via `_SLACK_REACTIONS` to Slack names, backed by
`SlackSDKClient.add_reaction` (reactions.add). Best-effort envelope, never raises.

## Why it exists

Wires Slack's agent-facing surface onto the MCP server. Five tools
(``slack_cli`` / ``slack_skill`` / ``slack_bind`` / ``slack_status`` /
``slack_unbind``) — same shape as Lark's tool list but the dispatcher
+ skill-lookup pair carries far more weight here because Slack has
~250 Web API methods we deliberately don't pre-wrap.

## Design decisions

- **One generic dispatcher (``slack_cli``) instead of N typed tools.**
  Pre-wrapping ``chat.postMessage`` / ``conversations.history`` /
  ``users.info`` / ``reactions.add`` / ... would hand-craft 250
  function signatures and re-implement Slack's parameter schemas. The
  dispatcher takes ``(method, args)`` and forwards to
  ``SlackSDKClient.api_call`` — Slack already validates the schema and
  returns precise error codes.
- **Paired with ``slack_skill`` for on-demand docs.** The agent learns
  any method's exact arg shape, required scope, and a working example
  by calling ``slack_skill(method)``. The pattern mirrors
  ``lark_cli`` + ``lark_skill`` so cross-channel agent muscle memory
  transfers.
- **Mode-name validation is a regex, not a whitelist.** Slack's method
  list grows; whitelisting would gate new methods on a code change.
  Instead we accept any dotted-lowercase string and warn (don't block)
  when the agent calls a method we don't have a skill doc for.
- **No-credential and bad-args paths return ``{"ok": false}`` envelopes.**
  Same shape as a real Slack API failure so the agent's error-handling
  code path is uniform — no "is this our error or Slack's?" branching.
- **``agent_id`` is a parameter on every tool.** MCP context per-agent
  is established at runtime by the framework; passing the id
  explicitly removes ambient-state assumptions and makes the tool
  callable from the bind endpoint test path with the same shape.
- **``slack_skill`` ignores ``agent_id`` today (``del agent_id``).**
  Kept in the signature to match the MCP contract and to preserve the
  seam for per-agent skill overrides (e.g. agent-specific cheat sheets).

## Upstream / downstream

- **Upstream**: ``SlackModule.register_mcp_tools`` calls
  ``register_slack_mcp_tools(mcp)`` on the FastMCP server the base
  class created.
- **Downstream**:
  - ``SlackCredentialManager`` (via ``_get_manager`` /
    ``_get_credential``) for the bind/status/unbind tools.
  - ``do_bind`` / ``do_test_connection`` from ``_slack_service.py`` —
    same logic the REST routes call.
  - ``get_skill_loader`` for ``slack_skill``.
  - ``SlackSDKClient.api_call`` for ``slack_cli``.

## Gotchas

- ``slack_cli``'s method-validation regex (``^[a-z][a-zA-Z0-9._]+$``)
  is intentionally loose — Slack methods are dotted lowercase, but
  some include digits (``apps.permissions.users.list``). Tightening
  this without checking the full method list will block real methods.
- The "unknown method" warning in ``slack_cli`` is just a log line.
  Don't escalate to error/refuse — agents legitimately discover new
  Slack methods between releases of our generated skill docs.
- Slack's rate-limit response (``rate_limited``) flows through
  ``api_call`` unmodified. Agents should be told to back off; the
  retry policy is **not** baked in here.
