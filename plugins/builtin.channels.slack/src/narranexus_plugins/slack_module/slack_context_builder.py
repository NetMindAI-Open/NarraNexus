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

from narranexus.platform.channel.channel_prompts import (
    ROOM_TYPE_DIRECT,
    ROOM_TYPE_GROUP,
)
from narranexus.platform.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from narranexus.platform.schema.parsed_message import ParsedMessage

from ._slack_credential_manager import SlackCredential
from .slack_sdk_client import SlackSDKClient


# Slack conversation-id prefixes. The id space is the only signal available
# here without an extra `conversations.info` round trip:
#   C… public channel      G… private channel AND multi-person DM
#   D… 1:1 DM              (anything else: treated as a group room)
# Only `D…` is a two-person conversation, so `G…` multi-person DMs
# deliberately get the GROUP protocol — several people in a room is exactly
# the situation the group discipline was tuned for, whatever Slack calls it.
_SLACK_DM_ID_PREFIX = "D"


def _is_slack_dm(chat_id: str) -> bool:
    """True for a 1:1 Slack DM.

    `room_type` used to be hard-coded to Group Room here, justified as "the
    API surface is identical either way and room_type is only a prompt
    label". It now SELECTS the Communication Protocol, so mislabelling a DM
    tells the agent it may stay silent on a 1:1 message — the 0802 WeChat
    failure, latent on Slack. Named helper + constant for the same reason
    the room types themselves became constants: this is a behavioural
    contract now, not a display string.
    """
    return (chat_id or "").startswith(_SLACK_DM_ID_PREFIX)


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
            "room_type": (
                ROOM_TYPE_DIRECT if _is_slack_dm(chat_id) else ROOM_TYPE_GROUP
            ),
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
