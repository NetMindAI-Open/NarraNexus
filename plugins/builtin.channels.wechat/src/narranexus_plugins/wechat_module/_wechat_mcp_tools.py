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
``backend/routes/channels/wechat.py``, not something the agent does. The trigger gives
the agent the inbound ``to_user_id`` + ``context_token`` in the prompt; the
agent calls ``wechat_send`` to reply (the trigger's ``extract_output`` scrapes
this call for the inbox record).
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from narranexus.platform.module_system.data_access import get_channel_credential_store

from ._wechat_credential_manager import _cred_from_raw
from .wechat_outbound import send_wechat_text


async def _get_credential(agent_id: str):
    # Read path via the ChannelCredentialStore seam (blueprint P2): DirectStore
    # locally, HttpStore -> owner-gated backend endpoint in cloud. Rebuild the
    # dataclass so send/status keep using cred.bot_token / cred.to_public_dict().
    raw = await get_channel_credential_store().get_credential("wechat", agent_id)
    return _cred_from_raw(raw) if raw is not None else None


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

        Deployment note: on the managed path the platform delivers by
        ``to_user_id`` alone and ``context_token`` is unused (it may be empty);
        only the direct-iLink fallback requires the inbound ``context_token``.
        So a managed deployment CAN start a message to a known ``to_user_id``,
        while direct-iLink is reply-only within the active inbound.

        Returns ``{"ok": bool, "error"?: str}``.
        """
        if not text or not text.strip():
            return {"ok": False, "error": "empty_text"}
        if not to_user_id:
            return {"ok": False, "error": "missing_to_user_id"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no WeChat account bound; bind one from the Channels panel"}

        ok = await send_wechat_text(
            agent_id=agent_id,
            credential=cred,
            to_user_id=to_user_id,
            context_token=context_token,
            text=text,
        )
        return {"ok": ok} if ok else {"ok": False, "error": "send_failed"}

    @mcp.tool()
    async def react_to_user_message(
        agent_id: str, room_id: str = "", message_id: str = "", emoji: str = "on_it"
    ) -> dict:
        """React to the user's message with an emoji — NOT supported on WeChat.

        Present so the ``react_to_user_message`` capability is uniform across IM
        channels, but the WeChat iLink gateway has no reaction API. Always
        returns the unsupported envelope; to acknowledge early, send a short
        ``wechat_send`` message instead.
        """
        return {
            "success": False,
            "reason": "reactions are not supported on WeChat; send a short message instead",
        }

    @mcp.tool()
    async def wechat_status(agent_id: str) -> dict:
        """Return the agent's WeChat binding status (NO raw token)."""
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": True, "data": None, "bound": False}
        public = cred.to_public_dict()
        public["bound"] = True
        return {"success": True, "data": public}

    @mcp.tool()
    async def wechat_unbind(agent_id: str) -> dict:
        """Remove this agent's WeChat binding."""
        return await get_channel_credential_store().unbind("wechat", agent_id)

    logger.info("WeChat MCP tools registered: wechat_send, wechat_status, wechat_unbind")
