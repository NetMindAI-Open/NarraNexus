"""
@file_name: cloud_bus.py
@author: NarraNexus
@date: 2026-04-02
@description: Cloud-hosted MessageBus stub implementation

Placeholder for a future cloud-backed MessageBus that communicates
via REST API. All methods raise NotImplementedError until implemented.
"""

from __future__ import annotations

from typing import List, Optional

from narranexus.platform.message_bus.message_bus_service import MessageBusService
from narranexus.platform.message_bus.schemas import BusAgentInfo, BusChannelMember, BusMessage


class CloudMessageBus(MessageBusService):
    """
    Cloud-hosted MessageBus stub.

    Will eventually communicate with a remote MessageBus API.
    Currently all methods raise NotImplementedError.

    Args:
        api_base_url: Base URL of the cloud MessageBus API.
        auth_token: Authentication token for API access.
    """

    def __init__(self, api_base_url: str, auth_token: str) -> None:
        self._api_base_url = api_base_url
        self._auth_token = auth_token

    async def send_message(
        self,
        from_agent: str,
        to_channel: str,
        content: str,
        msg_type: str = "text",
        mentions: Optional[List[str]] = None,
        attachments: Optional[List[dict]] = None,
        event_id: Optional[str] = None,
        sender_turn_source: Optional[str] = None,
        root_run_id: Optional[str] = None,
        routed_by: Optional[str] = None,
        # Appended LAST and kept there: `send_message` has positional callers,
        # and a parameter added in the middle silently rebinds every one of
        # them. Pinned by test_team_message_segments.
        segments: Optional[List[dict]] = None,
        *,
        part_index: int = 0,
        part_count: int = 0,
    ) -> str:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_messages(
        self, channel_id: str, since: Optional[str] = None, limit: int = 50
    ) -> List[BusMessage]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_recent_messages(
        self, channel_id: str, limit: int = 20
    ) -> List[BusMessage]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_message(self, message_id: str) -> Optional[BusMessage]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_messages_before(
        self, channel_id: str, before: str, limit: int = 50
    ) -> List[BusMessage]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_unread(
        self, agent_id: str, limit: Optional[int] = None
    ) -> List[BusMessage]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def mark_read(self, agent_id: str, message_ids: List[str]) -> None:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def send_to_agent(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        msg_type: str = "text",
        attachments: Optional[List[dict]] = None,
        sender_turn_source: Optional[str] = None,
        root_run_id: Optional[str] = None,
        event_id: Optional[str] = None,
        *,
        part_index: int = 0,
        part_count: int = 0,
    ) -> str:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def create_channel(
        self, name: str, members: List[str], channel_type: str = "group"
    ) -> str:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def join_channel(self, agent_id: str, channel_id: str) -> None:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def leave_channel(self, agent_id: str, channel_id: str) -> None:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def register_agent(
        self,
        agent_id: str,
        owner_user_id: str,
        capabilities: List[str],
        description: str,
        visibility: str = "private",
    ) -> None:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def search_agents(
        self, query: str, requester_agent_id: Optional[str] = None, limit: int = 10
    ) -> List[BusAgentInfo]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_pending_messages(
        self, agent_id: str, limit: int = 50, channel_id: Optional[str] = None
    ) -> List[BusMessage]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def ack_processed(self, agent_id: str, channel_id: str, up_to_timestamp: str) -> None:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def has_unread_before(
        self, agent_id: str, channel_id: str, before: str
    ) -> bool:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def has_message_from_turn(
        self, channel_id: str, from_agent: str, event_id: str
    ) -> bool:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def count_unread(self, agent_id: str) -> int:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def ack_read(self, agent_id: str, channel_id: str, up_to_timestamp: str) -> None:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def record_failure(self, message_id: str, agent_id: str, error: str) -> None:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_failure_count(self, message_id: str, agent_id: str) -> int:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_channel_members(self, channel_id: str) -> List[BusChannelMember]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def kick_member(self, channel_id: str, agent_id: str) -> None:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def get_agent_profile(self, agent_id: str) -> Optional[BusAgentInfo]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")
