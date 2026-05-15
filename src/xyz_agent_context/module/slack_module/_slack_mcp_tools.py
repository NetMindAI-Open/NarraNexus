"""
@file_name: _slack_mcp_tools.py
@date: 2026-05-08
@description: Slack MCP tools — generic Web API dispatcher + skill lookup
+ binding management.

Tools exposed (5 total):
  - slack_cli(agent_id, method, args)    — call ANY Slack Web API method
  - slack_skill(agent_id, method)        — fetch markdown docs for a method
  - slack_bind(agent_id, bot_token, app_token)   — bind a bot to an agent
  - slack_status(agent_id)               — sanitised status / health
  - slack_unbind(agent_id)               — remove binding

Mirror of Lark's `lark_cli` + `lark_skill` pair (one generic dispatcher +
one docs fetcher) so the agent-facing shape of "interact with channel X"
stays uniform across Lark / Slack / Telegram.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule

from ._slack_credential_manager import SlackCredentialManager
from ._slack_service import do_bind, do_test_connection
from ._slack_skill_loader import get_skill_loader
from .slack_sdk_client import SlackSDKClient


# Loose validation: dotted lowercase identifiers (Slack convention)
_VALID_METHOD_RE = re.compile(r"^[a-z][a-zA-Z0-9._]+$")


async def _get_credential(agent_id: str):
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = SlackCredentialManager(db)
    return await mgr.get(agent_id)


async def _get_manager() -> SlackCredentialManager:
    db = await XYZBaseModule.get_mcp_db_client()
    return SlackCredentialManager(db)


def register_slack_mcp_tools(mcp: Any) -> None:
    """Register Slack MCP tools on the given FastMCP server.

    Cross-agent guard note (2026-05-12): an earlier draft pinned the
    server to a single ``deployment_agent_id`` and rejected mismatching
    caller agent_ids. That broke the actual deployment model: the dev
    MCP server (``module_runner mcp``) is **multi-tenant** — one
    process serves every agent in the workspace, demuxing on the
    ``agent_id`` parameter of each tool call. Pinning broke legitimate
    calls because every module was constructed with the placeholder
    ``agent_id="mcp_deploy"``. Defence-in-depth against cross-agent
    tool calls needs to happen at the AgentRuntime → MCP transport
    layer (tagging the calling agent_id, not relying on tool
    parameters); a tool-level check can't distinguish "wrong caller"
    from "legitimate multi-tenant demux".
    """

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def slack_cli(agent_id: str, method: str, args: dict) -> dict:
        """Call any Slack Web API method with the agent's bound bot token.

        ``method`` is the dotted method name (e.g. ``chat.postMessage``,
        ``conversations.history``, ``reactions.add``, ``users.info``). See
        full method list at https://api.slack.com/methods.

        ``args`` is a JSON object of method-specific arguments. ALWAYS call
        ``slack_skill(agent_id, method)`` first if you don't already know
        the exact arg shape for that method — it returns the params table
        and example call from the OpenAPI-derived skill doc.

        Returns the raw Slack response envelope:
          - on success: ``{"ok": true, ...method-specific data...}``
          - on failure: ``{"ok": false, "error": "<slack_error_code>"}``

        Common Slack error codes:
          - ``invalid_auth`` — bot token revoked or wrong; bind again
          - ``channel_not_found`` — bot isn't in that channel; ask user to
            invite the bot
          - ``missing_scope`` — bot's OAuth scopes don't permit this
            method; user needs to add the scope in the Slack App config
          - ``rate_limited`` — back off + retry; do NOT spam
        """
        if not method or not _VALID_METHOD_RE.match(method):
            return {
                "ok": False,
                "error": "invalid_method_name",
                "hint": "method must be dotted lowercase, e.g. chat.postMessage",
            }
        if not isinstance(args, dict):
            return {"ok": False, "error": "args_must_be_object"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {
                "ok": False,
                "error": "no_credential",
                "hint": "no Slack bot bound; use slack_bind first",
            }

        # Warn (don't block) if the agent is calling a method we don't have
        # a skill doc for — could be brand new, but also could be a typo
        loader = get_skill_loader()
        if method not in loader.list_methods():
            logger.warning(
                f"[slack:{agent_id}] slack_cli called for unknown-to-us method '{method}'"
            )

        client = SlackSDKClient(cred.bot_token)
        return await client.api_call(method, args)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def slack_skill(agent_id: str, method: str) -> str:
        """Fetch the docs (args, scope, examples) for a Slack Web API method.

        Always call this BEFORE ``slack_cli`` for an unfamiliar method. The
        returned markdown has the exact arg shape, required OAuth scope,
        and an example invocation — saves you guessing and getting
        ``missing_scope`` / ``invalid_arguments`` errors.

        ``method`` is the dotted method name (e.g. ``chat.postMessage``).

        For an unknown method this returns a helpful hint listing
        same-category methods.
        """
        # agent_id is currently unused — kept in signature for symmetry with
        # lark_skill and to allow per-agent skill overrides in a later phase.
        del agent_id
        loader = get_skill_loader()
        return loader.get(method)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def slack_bind(
        agent_id: str, bot_token: str, app_token: str
    ) -> dict:
        """Bind a Slack workspace to this agent.

        ``bot_token`` starts with ``xoxb-`` (from OAuth & Permissions →
        Install App).
        ``app_token`` starts with ``xapp-`` (from Basic Information →
        App-Level Tokens, with ``connections:write`` scope, required for
        Socket Mode).

        Returns ``{"success": bool, "error"?: str, "data"?: {team_id,
        team_name, bot_user_id}}``.
        """
        mgr = await _get_manager()
        return await do_bind(mgr, agent_id, bot_token, app_token)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def slack_status(agent_id: str) -> dict:
        """Return sanitised Slack binding status (NO raw tokens).

        Re-runs ``auth.test`` so you see live connectivity, not just DB state.
        """
        mgr = await _get_manager()
        cred = await mgr.get(agent_id)
        if not cred:
            return {"success": True, "data": None, "bound": False}

        live = await do_test_connection(mgr, agent_id)
        public = cred.to_public_dict()
        public["bound"] = True
        public["live_check"] = live
        return {"success": True, "data": public}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def slack_unbind(agent_id: str) -> dict:
        """Remove this agent's Slack binding."""
        mgr = await _get_manager()
        removed = await mgr.unbind(agent_id)
        if not removed:
            return {"success": False, "error": "no Slack credential bound"}
        return {"success": True, "data": {"unbound": True}}

    logger.info("Slack MCP tools registered: slack_cli, slack_skill, slack_bind, slack_status, slack_unbind")
