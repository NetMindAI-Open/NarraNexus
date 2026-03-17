"""
@file_name: matrix_context_builder.py
@author: Bin Liang
@date: 2026-03-10
@description: MatrixContextBuilder — Matrix channel prompt constructor

Inherits from ChannelContextBuilderBase and implements Matrix-specific data
fetching. Uses NexusMatrixClient for conversation history, room members,
and optionally fetches sender capabilities from the Registry.

The prompt follows the standard sectioned assembly from the base class:
message info → sender profile → conversation history → message body → members → instructions
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from .matrix_client import NexusMatrixClient
from ._matrix_credential_manager import MatrixCredential


# Matrix-specific extra template for sender capabilities from Registry
MATRIX_SENDER_EXTRA_TEMPLATE = """\
- **Capabilities** (from Registry): {capabilities}
"""


class MatrixContextBuilder(ChannelContextBuilderBase):
    """
    Matrix channel prompt constructor.

    Inherits the base class's standard sectioned flow and implements
    Matrix-specific data fetching via NexusMatrixClient.

    Args:
        message_event: Raw message event dict from sync/heartbeat
        credential: Agent's MatrixCredential
        client: NexusMatrixClient instance
        agent_id: The Agent's ID in our system
    """

    def __init__(
        self,
        message_event: Dict[str, Any],
        credential: MatrixCredential,
        client: NexusMatrixClient,
        agent_id: str,
    ):
        self.event = message_event
        self.credential = credential
        self.client = client
        self.agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        """Return message metadata for template rendering."""
        return {
            "agent_id": self.agent_id,
            "channel_display_name": "Matrix",
            "channel_key": "matrix",
            "room_name": self.event.get("room_name", "Unknown Room"),
            "room_id": self.event.get("room_id", ""),
            "room_type": "Direct Message" if self.event.get("is_direct") else "Group Room",
            "sender_display_name": self.event.get("sender_display_name", "Unknown"),
            "sender_id": self.event.get("sender", ""),
            "timestamp": self.event.get("timestamp", ""),
            "my_channel_id": self.credential.matrix_user_id,
            "message_body": self.event.get("body", ""),
            "send_tool_name": "matrix_send_message",
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Fetch conversation history from NexusMatrix API."""
        try:
            messages = await self.client.get_messages(
                api_key=self.credential.api_key,
                room_id=self.event.get("room_id", ""),
                limit=limit,
            )
            return messages or []
        except Exception as e:
            logger.warning(f"Failed to fetch Matrix conversation history: {e}")
            return []

    async def get_room_members(self) -> List[Dict[str, Any]]:
        """Fetch room members from NexusMatrix API."""
        try:
            members = await self.client.get_room_members(
                api_key=self.credential.api_key,
                room_id=self.event.get("room_id", ""),
            )
            return members or []
        except Exception as e:
            logger.warning(f"Failed to fetch Matrix room members: {e}")
            return []

    async def get_sender_extra_profile(self) -> str:
        """Fetch sender's capabilities from NexusMatrix Registry."""
        try:
            sender_id = self.event.get("sender", "")
            if not sender_id:
                return ""

            # sender_id is a Matrix user ID (e.g. @agent_xxx:localhost),
            # not a NexusMatrix agent_id, so use lookup by matrix_user_id
            profile = await self.client.get_agent_by_matrix_user_id(
                api_key=self.credential.api_key,
                matrix_user_id=sender_id,
            )
            if profile and profile.get("capabilities"):
                caps = profile["capabilities"]
                if isinstance(caps, list):
                    return MATRIX_SENDER_EXTRA_TEMPLATE.format(
                        capabilities=", ".join(caps)
                    )
            return ""
        except Exception as e:
            logger.debug(f"Could not fetch sender Registry profile: {e}")
            return ""
