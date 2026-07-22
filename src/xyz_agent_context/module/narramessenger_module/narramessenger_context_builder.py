"""
@file_name: narramessenger_context_builder.py
@date: 2026-06-17
@description: Build execution context for NarraMessenger-triggered messages.

Inherits ``ChannelContextBuilderBase``. Post-Direct-Matrix migration
(Commit 7, 2026-07-02) the trigger reads raw ``m.room.message`` events
off ``/sync``; there is NO inline history or member roster on the
invocation payload — ``ParsedMessage.raw`` only carries the fields
``matrix_trigger._wrap_event`` populates (text / mxc_url / mimetype /
size / event_id / room_id / sender_id / server_ts).

Consequences for the abstract methods:
  - ``get_conversation_history`` → ``[]``. History is served by
    ChatModule's chat_history assembly during ``hook_data_gathering``
    (persisted turns + attachment markers all end up in the system
    prompt).
  - ``get_room_members`` → ``[]``. If a future turn actually needs a
    live roster, the agent calls ``narra_cli`` with
    ``room info --room-id <id> --members`` on demand. Baking the roster
    into every prompt is expensive and would duplicate what that command
    already returns per-agent-decision.

Only ``get_message_info`` still reads a bit of ``raw`` — the
``room_name`` from ``group_context.room.name`` (graceful ``""``
fallback when absent, which is the current default).
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
            'call `narra_reply(text="YOUR_REPLY")` — your reply is delivered '
            "to this NarraMessenger room automatically. Send exactly ONE "
            "message with your real answer (plain text / markdown). The room "
            "stays quiet until you call this tool — there is no intermediate "
            "status message, so if the work takes a while, just do it and send "
            "the final answer when ready. To attach an image or file, put it "
            "in your workspace first, then call "
            f'`narra_send_media(room_id="{room_id}", file_path="...")`.'
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
            "send_tool_name": "narra_reply",
            "reply_instruction": reply_instruction,
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Always returns an empty list — history is served from memory.

        Pre-Matrix (Gateway/polling) NarraMessenger shipped inline
        ``group_context.history_messages`` (group) / ``context`` (DM)
        in every invocation, and this method normalised them into the
        base's ``[{sender, timestamp, body}]`` shape so the
        ``## Conversation History`` slot in the channel prompt could
        carry a few rounds of context.

        Direct Matrix (Commit 7, 2026-07-02) removed that channel:
        ``matrix_trigger._wrap_event`` produces neither field in
        ``ParsedMessage.raw``, so every call returned ``[]`` via the
        fallback branch. The method now returns ``[]``
        unconditionally.

        The intended path is ChatModule's chat_history: past turns
        (including their attachments) are recalled from persisted
        messages during ``hook_data_gathering`` and rendered into the
        system prompt. Historical attachment markers are synthesised
        there via ``Attachment.markers_from_dicts``; the current
        turn's marker is appended at
        ``context_runtime.build_input_for_framework`` — same helper,
        same shape.
        """
        del limit  # signature contract only
        return []

    async def get_room_members(self) -> List[Dict[str, Any]]:
        """Always returns an empty list — no inline roster on Direct Matrix.

        Pre-Matrix Gateway shipped ``group_context.members`` (with
        ``matrix_user_id`` / ``display_name`` per member), and this
        method normalised them for the base's ``## Conversation
        Members`` section. Direct Matrix's ``_wrap_event`` produces no
        ``group_context`` at all — this method returned ``[]`` on
        every call anyway.

        If a specific turn needs the live roster, the agent calls
        ``narra_cli`` with ``room info --room-id <id> --members`` on
        demand. Baking the roster into every prompt would duplicate
        what that command already exposes per-agent-decision.
        """
        return []
