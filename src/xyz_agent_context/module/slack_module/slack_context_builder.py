"""
@file_name: slack_context_builder.py
@date: 2026-05-08
@description: Build execution context for Slack-triggered messages.

Inherits ChannelContextBuilderBase. Slack provides real conversation
history via `conversations.history` / `conversations.replies` (Telegram
does NOT — that's Telegram's only weakness). When the inbound message
carries a ``thread_ts`` we pull thread replies; otherwise we pull recent
channel history.
"""

from __future__ import annotations

from typing import Any, Dict, List

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.schema.parsed_message import ParsedMessage

from ._slack_credential_manager import SlackCredential
from .slack_sdk_client import SlackSDKClient


class SlackContextBuilder(ChannelContextBuilderBase):
    """Slack-specific context builder."""

    def __init__(
        self,
        message: ParsedMessage,
        credential: SlackCredential,
        agent_id: str,
    ):
        self._message = message
        self._credential = credential
        self._client = SlackSDKClient(credential.bot_token)
        self._agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        chat_id = self._message.chat_id
        thread_ts = self._message.thread_id or ""
        return {
            "agent_id": self._agent_id,
            "channel_display_name": "Slack",
            "channel_key": "slack",
            "room_name": "",  # could resolve via conversations.info — Phase 3 leaves blank
            "room_id": chat_id,
            "room_type": "Group Room",  # Slack channels behave as group conversations
            "sender_display_name": self._message.sender_name or self._message.sender_id,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": self._credential.bot_user_id,
            "message_body": self._message.content,
            "send_tool_name": "slack_cli",
            "reply_instruction": (
                f'call `slack_cli(method="chat.postMessage", '
                f'args={{"channel": "{chat_id}", "text": "YOUR_REPLY"'
                + (f', "thread_ts": "{thread_ts}"' if thread_ts else "")
                + "})`. Send exactly ONE message. Use Slack mrkdwn "
                "(`*bold*`, `_italic_`, `<URL|text>`) — NOT GitHub markdown."
            ),
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Slack history — newest-first from API, we reverse for chronological."""
        if not self._message.chat_id:
            return []

        if self._message.thread_id:
            messages = await self._client.get_conversation_replies(
                channel=self._message.chat_id,
                ts=self._message.thread_id,
                limit=limit,
            )
        else:
            messages = await self._client.get_conversation_history(
                channel=self._message.chat_id,
                limit=limit,
            )

        # Slack returns newest-first; reverse for chronological
        normalized: List[Dict[str, Any]] = []
        for m in reversed(messages):
            normalized.append({
                "timestamp": m.get("ts", ""),
                "sender": m.get("user", m.get("bot_id", "bot")),
                "body": m.get("text", ""),
            })
        return normalized

    async def get_room_members(self) -> List[Dict[str, Any]]:
        # Phase 3 doesn't resolve workspace members — Slack channels can be
        # huge (thousands of users) and we'd burn rate-limit budget.
        return []
