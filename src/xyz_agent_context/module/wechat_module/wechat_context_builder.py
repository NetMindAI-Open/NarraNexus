"""
@file_name: wechat_context_builder.py
@author:
@date: 2026-06-24
@description: Build execution context for WeChat (iLink) triggered messages.

Mirrors ``telegram_module/telegram_context_builder.py``. iLink, like Telegram's
Bot API, exposes no server-side history endpoint, so history falls back to the
local ``bus_messages`` table that ``ChannelInboxWriter`` populates under
``channel_id = f"wechat_{to_user_id}"``.

The reply contract: the agent replies by calling the ``wechat_send`` MCP tool
with the inbound ``to_user_id`` + ``context_token`` (surfaced here in
``reply_instruction``). v1 is DM-only (personal account, 1:1).
"""
from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.schema.parsed_message import ParsedMessage

from ._wechat_credential_manager import WeChatCredential


class WeChatContextBuilder(ChannelContextBuilderBase):
    """WeChat-specific context builder."""

    def __init__(
        self,
        message: ParsedMessage,
        credential: WeChatCredential,
        agent_id: str,
        db_client: Any = None,
    ):
        self._message = message
        self._credential = credential
        self._agent_id = agent_id
        self._db = db_client

    async def get_message_info(self) -> Dict[str, Any]:
        to_user_id = self._message.chat_id
        context_token = (self._message.raw or {}).get("context_token", "") or ""
        reply_instruction = (
            f'call `wechat_send(to_user_id="{to_user_id}", '
            f'context_token="{context_token}", text="YOUR_REPLY")`. Send exactly '
            f"ONE message. Use plain text — WeChat has no markdown rendering, so "
            f"asterisks / backticks show up literally."
        )
        return {
            "agent_id": self._agent_id,
            "channel_display_name": "WeChat",
            "channel_key": "wechat",
            "room_name": "",
            "room_id": to_user_id,
            "room_type": "Direct Message",  # v1: personal-account DM only
            "sender_display_name": self._message.sender_name or self._message.sender_id,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": self._credential.bot_wx_id,
            "message_body": self._message.content,
            "send_tool_name": "wechat_send",
            "reply_instruction": reply_instruction,
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Read recent turns from local ``bus_messages`` (no iLink history API)."""
        if not self._db or not self._message.chat_id:
            return []

        channel_id = f"wechat_{self._message.chat_id}"
        fetch_n = max(limit + 5, 10)
        try:
            rows = await self._db.get(
                "bus_messages",
                {"channel_id": channel_id},
                limit=fetch_n,
                order_by="created_at DESC",
            )
        except Exception as e:  # noqa: BLE001 — history is best-effort
            logger.warning(
                f"[wechat:{self._agent_id}] history fetch failed "
                f"(channel={channel_id}): {type(e).__name__}: {e}"
            )
            return []

        current_id = self._message.message_id
        normalized: List[Dict[str, Any]] = []
        for row in reversed(rows):
            from_agent = row.get("from_agent", "") or ""
            content = row.get("content", "") or ""
            row_msg_id = row.get("message_id", "") or ""
            if current_id and row_msg_id == current_id:
                continue
            is_bot = from_agent == self._agent_id
            sender = "Me (bot)" if is_bot else (
                self._message.sender_name or from_agent
            )
            normalized.append({
                "timestamp": str(row.get("created_at", "")),
                "sender": sender,
                "body": content,
            })

        if len(normalized) > limit:
            normalized = normalized[-limit:]
        return normalized

    async def get_room_members(self) -> List[Dict[str, Any]]:
        # v1 is 1:1 DM — no member enumeration.
        return []
