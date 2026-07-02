"""
@file_name: discord_context_builder.py
@date: 2026-06-16
@description: Build execution context for Discord-triggered messages.

Inherits ChannelContextBuilderBase. Discord provides real conversation
history via ``GET /channels/{id}/messages`` (newest-first), which we
reverse for chronological order. Reply instructions point the agent at
the messaging-first MCP tools (``discord_send`` / ``discord_reply``).
"""
from __future__ import annotations

from typing import Any, Dict, List

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.schema.parsed_message import ParsedMessage

from ._discord_credential_manager import DiscordCredential
from .discord_sdk_client import DiscordSDKClient


class DiscordContextBuilder(ChannelContextBuilderBase):
    """Discord-specific context builder."""

    def __init__(
        self,
        message: ParsedMessage,
        credential: DiscordCredential,
        agent_id: str,
    ):
        self._message = message
        self._credential = credential
        self._client = DiscordSDKClient(credential.bot_token)
        self._agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        chat_id = self._message.chat_id
        message_id = self._message.message_id
        is_dm = (self._message.raw or {}).get("is_dm", False)
        return {
            "agent_id": self._agent_id,
            "channel_display_name": "Discord",
            "channel_key": "discord",
            "room_name": "",  # could resolve via GET /channels/{id} — left blank for now
            "room_id": chat_id,
            "room_type": "Direct Message" if is_dm else "Group Room",
            "sender_display_name": self._message.sender_name or self._message.sender_id,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": self._credential.bot_user_id,
            "message_body": self._message.content,
            "send_tool_name": "discord_send",
            "reply_instruction": (
                f'call `discord_reply(channel_id="{chat_id}", '
                f'message_id="{message_id}", text="YOUR_REPLY")` to reply '
                f'inline, or `discord_send(channel_id="{chat_id}", '
                f'text="YOUR_REPLY")` for a plain message. Send exactly ONE '
                f'reply. Use standard markdown (`**bold**`, `*italic*`, '
                f'`` `code` ``) — Discord renders it natively. Keep replies '
                f'under 2000 characters where possible (longer text is split '
                f'into multiple messages automatically).'
            ),
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Discord history — newest-first from REST, reversed for chronological."""
        if not self._message.chat_id:
            return []
        messages = await self._client.get_channel_messages(
            channel_id=self._message.chat_id, limit=limit
        )
        normalized: List[Dict[str, Any]] = []
        for m in reversed(messages):
            author = m.get("author", {}) if isinstance(m, dict) else {}
            normalized.append(
                {
                    "timestamp": m.get("timestamp", ""),
                    "sender": str(author.get("id", "")) or "unknown",
                    "body": m.get("content", ""),
                }
            )
        return normalized

    async def get_room_members(self) -> List[Dict[str, Any]]:
        # Guild member lists can be huge and need a privileged intent /
        # paginated GET; we don't surface them in the prompt.
        return []
