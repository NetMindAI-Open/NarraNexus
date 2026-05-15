"""
@file_name: _telegram_mcp_tools.py
@date: 2026-05-09
@description: Telegram MCP tools — generic Bot API dispatcher + skill
lookup + binding management.

Tools exposed (5 total):
  - tg_cli(agent_id, method, args)    — call ANY Telegram Bot API method
  - tg_skill(agent_id, method)        — fetch markdown docs for a method
  - tg_bind(agent_id, bot_token, owner_username="")
  - tg_status(agent_id)
  - tg_unbind(agent_id)

Mirrors Slack's ``slack_cli`` + ``slack_skill`` pair so the agent-facing
shape of "interact with channel X" stays uniform.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule

from ._telegram_credential_manager import TelegramCredentialManager
from ._telegram_service import do_bind, do_test_connection
from ._telegram_skill_loader import get_skill_loader
from .telegram_sdk_client import TelegramSDKClient


# Telegram Bot API methods are camelCase — no dots, unlike Slack.
_VALID_METHOD_RE = re.compile(r"^[a-z][a-zA-Z0-9]+$")


async def _get_credential(agent_id: str):
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = TelegramCredentialManager(db)
    return await mgr.get(agent_id)


async def _get_manager() -> TelegramCredentialManager:
    db = await XYZBaseModule.get_mcp_db_client()
    return TelegramCredentialManager(db)


def register_telegram_mcp_tools(mcp: Any) -> None:
    """Register Telegram MCP tools on the given FastMCP server.

    See ``register_slack_mcp_tools`` for the note on why caller agent_id
    is NOT verified at this layer — the dev MCP server is multi-tenant.
    """

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tg_cli(agent_id: str, method: str, args: dict) -> dict:
        """Call any Telegram Bot API method with the agent's bound bot token.

        ``method`` is the camelCase method name (e.g. ``sendMessage``,
        ``getUpdates``, ``getChat``, ``editMessageText``). See full list
        at https://core.telegram.org/bots/api.

        ``args`` is a JSON object of method-specific arguments. ALWAYS
        call ``tg_skill(agent_id, method)`` first if you don't already
        know the exact arg shape — the skill doc has the params table.

        Returns Telegram's native envelope:
          - on success: ``{"ok": true, "result": <method-specific data>}``
          - on failure: ``{"ok": false, "error": "<description>"}``

        Common Bot API errors:
          - ``Unauthorized`` — token revoked at @BotFather; rebind
          - ``chat not found`` — bot isn't in that chat or chat_id wrong
          - ``Forbidden: bot was blocked by the user`` — user blocked bot;
            don't retry
          - ``Bad Request: message text is empty`` — text arg missing
          - ``Conflict`` — webhook is set; deleteWebhook first

        Privacy mode reminder: in groups, the bot only RECEIVES messages
        that @-mention it OR start with ``/``. This is the recommended
        default — it gives @-mention-only group UX. tg_cli SEND is NOT
        affected by privacy mode (you can chat.postMessage / sendMessage
        to any chat the bot has joined regardless).
        """
        if not method or not _VALID_METHOD_RE.match(method):
            return {
                "ok": False,
                "error": "invalid_method_name",
                "hint": "method must be camelCase, e.g. sendMessage",
            }
        if not isinstance(args, dict):
            return {"ok": False, "error": "args_must_be_object"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {
                "ok": False,
                "error": "no_credential",
                "hint": "no Telegram bot bound; use tg_bind first",
            }

        # Warn if calling a method we don't have a skill doc for —
        # could be brand new (sendPaidMedia etc.) or could be a typo.
        loader = get_skill_loader()
        if method not in loader.list_methods():
            logger.info(
                f"[telegram:{agent_id}] tg_cli called for non-curated method "
                f"'{method}' (Telegram has ~100 methods; we curate ~25)"
            )

        client = TelegramSDKClient(cred.bot_token)
        try:
            return await client.api_call(method, args)
        finally:
            await client.close()

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tg_skill(agent_id: str, method: str) -> str:
        """Fetch the docs (args, scope, examples) for a Telegram Bot API method.

        Always call this BEFORE ``tg_cli`` for an unfamiliar method. The
        returned markdown has the params table and a sample invocation.

        ``method`` is the camelCase name (e.g. ``sendMessage``).

        For unknown methods returns a friendly hint listing available
        categories.
        """
        del agent_id  # unused — kept for symmetry with lark_skill / slack_skill
        loader = get_skill_loader()
        return loader.get(method)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tg_bind(
        agent_id: str, bot_token: str, owner_username: str = ""
    ) -> dict:
        """Bind a Telegram bot to this agent.

        ``bot_token`` is the token from @BotFather, format
        ``<digits>:<base64>`` (e.g. ``7981632450:AAH-kxRP...``).

        ``owner_username`` is OPTIONAL — supply the user's Telegram
        @username (e.g. ``@bin_liang`` or ``bin_liang``) so the agent
        can later distinguish owner from stranger. We resolve it via
        ``getChat("@handle")`` to get the immutable numeric user_id.
        Without it, no trust signal — agent treats every Telegram
        sender as untrusted.

        Returns ``{"success": bool, "error"?: str, "data"?: {...}}``.
        """
        mgr = await _get_manager()
        return await do_bind(mgr, agent_id, bot_token, owner_username)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tg_status(agent_id: str) -> dict:
        """Return sanitised Telegram binding status (NO raw token).

        Re-runs ``getMe`` so you see live connectivity, not just DB
        state.
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
    async def tg_unbind(agent_id: str) -> dict:
        """Remove this agent's Telegram binding."""
        mgr = await _get_manager()
        removed = await mgr.unbind(agent_id)
        if not removed:
            return {"success": False, "error": "no Telegram credential bound"}
        return {"success": True, "data": {"unbound": True}}

    logger.info(
        "Telegram MCP tools registered: tg_cli, tg_skill, tg_bind, tg_status, tg_unbind"
    )
