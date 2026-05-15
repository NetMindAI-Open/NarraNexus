---
code_file: src/xyz_agent_context/module/slack_module/slack_module.py
stub: false
last_verified: 2026-05-12
---

## Why it exists

Phase 3 of the IM channel abstraction (see
``reference/self_notebook/specs/2026-05-08-im-integration-design.md`` § 9).
Slack's ``ChannelModuleBase`` subclass — wires platform-specific prompt
content, credential schema, sender, and MCP tool registration to the
shared mechanism.

The deliberate contrast against Lark: Slack has **no admin approval
flow, no three-click identity dance, no per-tenant app**. The owner
pastes two tokens and the bot is live. So this file is much smaller
than ``LarkModule`` — the prompt simply names ``slack_cli`` /
``slack_skill`` and tells the agent how to look up methods on demand.

## Design decisions

- **Prompt stays small (~80 lines), capability disclosed via
  ``slack_skill``.** Slack has ~250 Web API methods; baking each into
  the system prompt would balloon every turn's token cost. Instead the
  prompt teaches the discovery pattern (``slack_skill(method)`` first,
  then ``slack_cli``) and lists only common methods by purpose.
- **Two prompt modes only.** ``_NO_BOT_INSTRUCTION`` (discovery, when
  no credential bound) and the operational template (when bound).
  Lark needs more states because the binding flow itself is multi-step;
  Slack's bind is atomic from the agent's perspective.
- **``_NO_BOT_INSTRUCTION`` embeds the full Slack App Manifest YAML.**
  Slack requires ~16 OAuth scopes, 5 event subscriptions, Socket Mode,
  and a bot user — any miss causes silent failures (``missing_scope``
  on send, no events received). The manifest pre-configures every
  setting in one paste; without it, agents and users were burning ~15
  minutes on click-through-and-figure-it-out flows, then debugging
  ``missing_scope`` errors. Manifest lives in the constant
  ``SLACK_APP_MANIFEST_YAML`` at the top of this file.
- **Manifest is duplicated in the frontend** (``SlackConfig.tsx`` has
  the same YAML). One round-trip-free render in the dashboard
  disclosure beats a single source via API. When Slack adds a scope
  we need, ``grep "channels:history" src/ frontend/src`` finds both
  copies — the diff stays small.
- **``WorkingSource`` comparison handles both enum and string.** Same
  Python 3.11+ ``str(enum)`` quirk documented on
  ``ChannelModuleBase`` — we compare against ``.value`` as a fallback.
- **MCP port 7831.** Reserved for Slack in the channel-port range
  (``7831`` Slack, future ``7832`` Telegram). Distinct from the
  Module-port range (7801-7806) because channels are infrastructure,
  not agent capabilities.
- **``send_to_agent`` returns plain dicts, never raises.** The cross-
  channel sender API expects ``{"success": bool, ...}`` so callers can
  branch without wrapping in try/except. Slack errors come back as
  string codes (``invalid_auth``, ``channel_not_found``) lifted from
  ``SlackSDKError.code``.
- **``_on_event_executed`` is a no-op stub.** Phase 3 doesn't push
  delivery telemetry back to Slack. Hook stays declared so a future
  phase (read receipts, success reactions) has the seam.
- **``build_extra_data`` exposes only public fields.** Tokens never
  leave ``SlackCredential.bot_token`` / ``app_token``; what flows into
  ``ctx_data.extra_data["slack_info"]`` is identity-only
  (team_id/team_name/bot_user_id/enabled).
- **Phase 5 reply policy is encoded in three layers.** (a) Iron rule
  #1 in ``_SLACK_IRON_RULES`` tells the agent it replies only when
  @-mentioned in channels; (b) the manifest under
  ``SLACK_APP_MANIFEST_YAML`` no longer subscribes to
  ``message.channels`` / ``message.groups`` so the bot is never sent
  those events; (c) the trigger's ``_ACCEPTED_MESSAGE_CHANNEL_TYPES``
  allow-list filters out anything Slack delivers anyway (e.g. because
  an older bind still has the scope). L2 (manifest) is the cheapest,
  L2.5 (trigger filter) is load-bearing, L1 (prompt) is the user-
  visible backstop. Edits must keep all three in sync — see
  ``slack_trigger.md`` for the filter; ``SlackConfig.tsx`` for the
  frontend manifest copy.

## Upstream / downstream

- **Upstream**: ``ChannelModuleBase`` (Phase 2 base — provides sender
  registration, ``hook_data_gathering`` template, MCP server glue).
- **Downstream**:
  - ``SlackCredentialManager`` — credential CRUD + auth.test validation.
  - ``register_slack_mcp_tools`` — registers the 5 MCP tools on the
    server the base creates.
  - ``SlackSDKClient`` — the thin slack_sdk wrapper used for the sender.
  - ``WorkingSource.SLACK`` — enum entry that ties Slack messages back
    through ``hook_after_event_execution``.

## Gotchas

- The bound-state prompt embeds ``team_name`` / ``bot_user_id`` from
  ``ctx_data.extra_data["slack_info"]``. If ``build_extra_data``
  shape ever changes, the f-string breaks silently (renders empty).
- ``priority=6`` is intentional (placed after Lark=5 and the core
  capability modules). Reordering changes prompt section order.
- Iron rule #5 ("look up unknown methods first") is what keeps the
  agent from spamming ``slack_cli`` with guessed arg shapes — do not
  remove without rethinking the discovery pattern.
- ``SLACK_APP_MANIFEST_YAML`` is a load-bearing constant — it's the
  full setup recipe. Editing it (e.g. removing a scope) silently
  breaks every NEW bind from that point on. Existing binds keep their
  scopes from when they were granted.
- ``features.app_home.messages_tab_enabled`` is **non-obvious but
  load-bearing**. The DM scopes (``im:history``/``read``/``write``)
  and the ``message.im`` event subscription only handle delivery — they
  do NOT make the "Message" entry point appear in the Slack sidebar's
  Apps section. Without ``messages_tab_enabled: true``, users have to
  discover the bot via global search or @-mention. Drop this and the
  bot looks "broken" to anyone expecting the standard one-click DM
  flow.
