"""
@file_name: _wechat_mcp_tools.py
@author:
@date: 2026-06-24
@description: WeChat (iLink) MCP tools — the agent's reply path + status.

Tools exposed:
  - wechat_send(agent_id, to_user_id, context_token, text) — send a DM reply
  - wechat_status(agent_id)                                 — binding status
  - wechat_unbind(agent_id)                                 — remove binding

Mirrors ``telegram_module/_telegram_mcp_tools.py``. Unlike Telegram there is no
``wechat_bind`` tool — binding is a QR-scan flow driven by the Brain-panel UI +
``backend/routes/wechat.py``, not something the agent does. The trigger gives
the agent the inbound ``to_user_id`` + ``context_token`` in the prompt; the
agent calls ``wechat_send`` to reply (the trigger's ``extract_output`` scrapes
this call for the inbox record).
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule

from ._wechat_credential_manager import WeChatCredentialManager
from .wechat_sdk_client import send_text_once


async def _get_manager() -> WeChatCredentialManager:
    db = await XYZBaseModule.get_mcp_db_client()
    return WeChatCredentialManager(db)


def register_wechat_mcp_tools(mcp: Any) -> None:
    """Register WeChat MCP tools on the given FastMCP server."""

    @mcp.tool()
    async def wechat_send(
        agent_id: str, to_user_id: str, context_token: str, text: str
    ) -> dict:
        """Send a WeChat DM reply to the user who just messaged you.

        ``to_user_id`` + ``context_token`` come from the inbound message (they
        are given to you in the message context). ``text`` is your reply —
        plain text only (WeChat renders no markdown). Send exactly ONE message.

        Returns ``{"ok": bool, "error"?: str}``.
        """
        if not text or not text.strip():
            return {"ok": False, "error": "empty_text"}
        if not to_user_id:
            return {"ok": False, "error": "missing_to_user_id"}

        mgr = await _get_manager()
        cred = await mgr.get(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no WeChat account bound; bind one from the Channels panel"}

        ok = await send_text_once(
            cred.bot_token, cred.base_url, to_user_id, context_token, text
        )
        return {"ok": ok} if ok else {"ok": False, "error": "send_failed"}

    @mcp.tool()
    async def wechat_status(agent_id: str) -> dict:
        """Return the agent's WeChat binding status (NO raw token)."""
        mgr = await _get_manager()
        cred = await mgr.get(agent_id)
        if not cred:
            return {"success": True, "data": None, "bound": False}
        public = cred.to_public_dict()
        public["bound"] = True
        return {"success": True, "data": public}

    @mcp.tool()
    async def wechat_unbind(agent_id: str) -> dict:
        """Remove this agent's WeChat binding."""
        mgr = await _get_manager()
        removed = await mgr.unbind(agent_id)
        if not removed:
            return {"success": False, "error": "no WeChat credential bound"}
        return {"success": True, "data": {"unbound": True}}

    logger.info("WeChat MCP tools registered: wechat_send, wechat_status, wechat_unbind")
