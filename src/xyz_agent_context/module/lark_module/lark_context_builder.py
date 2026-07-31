"""
@file_name: lark_context_builder.py
@date: 2026-04-10
@description: Build execution context for Lark-triggered messages.

Inherits ChannelContextBuilderBase and implements Lark-specific data fetching
(message info, conversation history, room members).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from ._lark_credential_manager import LarkCredential
from .lark_cli_client import LarkCLIClient


class LarkContextBuilder(ChannelContextBuilderBase):
    """
    Lark-specific context builder for trigger-originated messages.

    Fetches conversation history and member info via lark-cli.
    """

    def __init__(
        self,
        event: Dict[str, Any],
        credential: LarkCredential,
        cli: LarkCLIClient,
        agent_id: str,
    ):
        self.event = event
        self.credential = credential
        self.cli = cli
        self.agent_id = agent_id

    # Appended to reply_instruction for group rooms only.
    #
    # The trigger only wakes the agent when it was @-mentioned
    # (`LarkTrigger.is_group_reply_warranted`), so by the time this runs
    # someone deliberately addressed the bot — mid-conversation, with
    # context the agent never saw. The pre-fetched history window is
    # capped, and the interesting part is often the exchange just above
    # the mention. Tell the agent to go widen its own view before
    # answering rather than replying to an isolated sentence.
    _GROUP_ROOM_INSTRUCTION = (
        "\n\nThis is a GROUP room and you were @-mentioned — you are only "
        "woken for group messages that mention you, so the conversation "
        "above almost certainly carries context you have not seen. BEFORE "
        "replying, make a genuine effort to read the recent history: call "
        '`lark_cli(agent_id="{agent_id}", command="im +chat-messages-list '
        '--chat-id {chat_id} --page-size 50")`, and widen with '
        "`im +messages-search` (needs `--as user`) when the thread points "
        "further back or to another room. Read what people actually said, "
        "who asked what, and whether the question was already answered — "
        "then reply once, addressing the person who mentioned you. If a "
        "history call fails on permissions, say what you could not read "
        "instead of guessing at the missing context."
    )

    async def get_message_info(self) -> Dict[str, Any]:
        brand_display = "Lark" if self.credential.brand == "lark" else "Feishu"
        chat_type = self.event.get("chat_type", "p2p")
        room_type = "Direct Message" if chat_type == "p2p" else "Group Room"
        return {
            "agent_id": self.agent_id,
            "channel_display_name": brand_display,
            "channel_key": "lark",
            "room_name": self.event.get("chat_name", ""),
            "room_id": self.event.get("chat_id", ""),
            "room_type": room_type,
            "sender_display_name": self.event.get("sender_name", "Unknown"),
            "sender_id": self.event.get("sender_id", ""),
            "timestamp": self.event.get("create_time", ""),
            "my_channel_id": self.credential.app_id,
            "message_body": self.event.get("content", ""),
            "send_tool_name": "lark_cli",
            "reply_instruction": (
                'call `lark_cli(agent_id="{agent_id}", command="im +messages-send '
                '--chat-id {chat_id} --markdown YOUR_REPLY")`. '
                "Send exactly ONE message. Prefer `--markdown` so headings, "
                "bold, bullets and line breaks render as Lark rich text "
                "(the CLI converts it to Lark post format automatically). "
                "Use `--text` only when the reply must keep exact verbatim "
                "layout — e.g. code blocks where spacing matters or ASCII art."
                + (self._GROUP_ROOM_INSTRUCTION if chat_type == "group" else "")
            ).format(agent_id=self.agent_id, chat_id=self.event.get("chat_id", "")),
        }

    async def get_conversation_history(self, limit: int) -> List[Dict]:
        chat_id = self.event.get("chat_id", "")
        if not chat_id:
            return []
        result = await self.cli.list_chat_messages(
            self.credential.agent_id,
            chat_id=chat_id,
            limit=limit,
        )
        if not result.get("success"):
            return []

        data = result.get("data", {})
        # CLI wraps in {"ok": true, "data": {...}} — unwrap
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if isinstance(data, dict):
            raw_msgs = data.get("items", data.get("messages", []))
        elif isinstance(data, list):
            raw_msgs = data
        else:
            return []

        # Map Lark CLI fields to ChannelContextBuilderBase expected format:
        # { "timestamp": str, "sender": str, "body": str }
        normalized = []
        for msg in raw_msgs:
            if isinstance(msg, dict):
                # Lark CLI may use: create_time, sender_id, content, body, text
                body = msg.get("content", msg.get("body", msg.get("text", "")))
                # content may be JSON-encoded (e.g. {"text": "hi"})
                if isinstance(body, str) and body.startswith("{"):
                    try:
                        body = json.loads(body).get("text", body)
                    except (json.JSONDecodeError, TypeError):
                        pass
                normalized.append({
                    "timestamp": msg.get("create_time", msg.get("timestamp", "")),
                    "sender": msg.get("sender_id", msg.get("sender", msg.get("from_id", "unknown"))),
                    "body": body,
                })
        return normalized

    async def get_room_members(self) -> List[Dict]:
        # Lark CLI doesn't have a direct +chat-members shortcut.
        # Can be implemented via API layer if needed.
        return []
