"""
@file_name: narramessenger_context_builder.py
@date: 2026-06-17
@description: Build execution context for NarraMessenger-triggered messages.

Inherits ChannelContextBuilderBase. Unlike Telegram (which keeps its own
``bus_messages`` log), NarraMessenger ships conversation context INLINE in
every invocation:
  - DM:    ``context`` = recent ``[{role, sender, content}]`` (up to ~20).
  - Group: ``group_context.history_messages`` + ``compressed_context`` +
           ``members`` (with role / identity_badge / governance_status).

So ``get_conversation_history`` / ``get_room_members`` read straight from
``ParsedMessage.raw`` (the full invocation), no extra API call.
"""

from __future__ import annotations

from typing import Any, Dict, List

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage

from ._narramessenger_credential_manager import NarramessengerCredential


class NarramessengerContextBuilder(ChannelContextBuilderBase):
    """NarraMessenger-specific context builder."""

    def __init__(
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
        agent_id: str,
    ):
        self._message = message
        self._credential = credential
        self._agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        room_id = self._message.chat_id
        is_group = self._message.chat_type == ChatType.GROUP

        gc = (self._message.raw or {}).get("group_context") or {}
        room_name = ((gc.get("room") or {}).get("name")) or ""

        reply_instruction = (
            f'call `narra_send(room_id="{room_id}", text="YOUR_REPLY")`. '
            f"Send exactly ONE message with your real answer (plain text)."
        )

        return {
            "agent_id": self._agent_id,
            "channel_display_name": "NarraMessenger",
            "channel_key": "narramessenger",
            "room_name": room_name,
            "room_id": room_id,
            "room_type": "Group Room" if is_group else "Direct Message",
            "sender_display_name": self._message.sender_name or self._message.sender_id,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": self._credential.matrix_user_id,
            "message_body": self._message.content,
            "send_tool_name": "narra_send",
            "reply_instruction": reply_instruction,
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Read recent turns from the invocation payload itself.

        Group invocations expose richer ``group_context.history_messages``;
        DM invocations carry ``context`` (``[{role, sender, content}]``). We
        normalise either into ``[{sender, timestamp, body}]`` and drop the
        current trigger message (the last user turn), which the prompt renders
        separately as the "Current Message" section.
        """
        raw = self._message.raw or {}
        gc = raw.get("group_context") or {}

        entries: List[Dict[str, Any]] = []
        history_messages = gc.get("history_messages")
        if isinstance(history_messages, list) and history_messages:
            for m in history_messages:
                if not isinstance(m, dict):
                    continue
                entries.append({
                    "sender": m.get("sender_display_name")
                    or m.get("sender_matrix_user_id")
                    or m.get("sender", "unknown"),
                    "timestamp": str(m.get("origin_server_ts", "") or m.get("sent_at", "")),
                    "body": m.get("body", "") or m.get("content", ""),
                })
        else:
            context = raw.get("context")
            if isinstance(context, list):
                for c in context:
                    if not isinstance(c, dict):
                        continue
                    sender = c.get("sender", "") or ""
                    is_bot = bool(sender) and sender == self._credential.matrix_user_id
                    entries.append({
                        "sender": "Me (agent)" if is_bot else (sender or c.get("role", "user")),
                        "timestamp": "",
                        "body": c.get("content", ""),
                    })

        # Drop a trailing entry that duplicates the current trigger message.
        if entries and entries[-1].get("body", "").strip() == (self._message.content or "").strip():
            entries = entries[:-1]

        if len(entries) > limit:
            entries = entries[-limit:]
        return entries

    async def get_room_members(self) -> List[Dict[str, Any]]:
        """Group member list from ``group_context.members``. DM → empty (the
        base hides the members section for <=2 members anyway)."""
        gc = (self._message.raw or {}).get("group_context") or {}
        members = gc.get("members")
        if not isinstance(members, list):
            return []
        out: List[Dict[str, Any]] = []
        for m in members:
            if not isinstance(m, dict):
                continue
            uid = m.get("matrix_user_id", "") or ""
            if not uid:
                continue
            out.append({
                "user_id": uid,
                "display_name": m.get("display_name", "") or uid,
            })
        return out
