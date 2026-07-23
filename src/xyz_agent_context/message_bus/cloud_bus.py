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

from xyz_agent_context.message_bus.message_bus_service import MessageBusService
from xyz_agent_context.message_bus.schemas import BusAgentInfo, BusChannelMember, BusMessage


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

    async def get_unread(self, agent_id: str) -> List[BusMessage]:
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

    async def get_pending_messages(self, agent_id: str, limit: int = 50) -> List[BusMessage]:
        raise NotImplementedError("Cloud MessageBus not yet implemented")

    async def ack_processed(self, agent_id: str, channel_id: str, up_to_timestamp: str) -> None:
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
