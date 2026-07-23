"""
@file_name: message_bus_service.py
@author: NarraNexus
@date: 2026-04-02
@description: Abstract base class defining the MessageBus service interface

All MessageBus implementations (local SQLite, cloud API, etc.) must implement
this interface. Covers messaging, channel management, agent discovery, and
delivery tracking (cursor-based processing model).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from xyz_agent_context.message_bus.schemas import BusAgentInfo, BusChannelMember, BusMessage


class MessageBusService(ABC):
    """
    Abstract interface for inter-agent communication.

    Implementations provide messaging, channel management, agent discovery,
    and delivery tracking. The delivery model uses per-agent cursors
    (last_processed_at) to track which messages have been consumed.
    """

    # ===== Messaging =====

    @abstractmethod
    async def send_message(
        self,
        from_agent: str,
        to_channel: str,
        content: str,
        msg_type: str = "text",
        mentions: Optional[List[str]] = None,
        attachments: Optional[List[dict]] = None,
    ) -> str:
        """
        Send a message to a channel.

        Args:
            from_agent: The agent ID of the sender.
            to_channel: The channel ID to send the message to.
            content: The message content.
            msg_type: The message type (default: "text").
            mentions: List of agent_ids to mention, or ["@everyone"].
            attachments: Optional list of bus-attachment dicts (see
                _bus_attachment_impl); files travel by reference, not bytes.

        Returns:
            The generated message_id.
        """
        ...

    @abstractmethod
    async def get_messages(
        self,
        channel_id: str,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> List[BusMessage]:
        """
        Get messages from a channel, optionally filtered by timestamp.

        Args:
            channel_id: The channel to fetch messages from.
            since: ISO 8601 timestamp; only return messages after this time.
            limit: Maximum number of messages to return.

        Returns:
            List of BusMessage, ordered by created_at ASC.
        """
        ...

    @abstractmethod
    async def get_recent_messages(
        self,
        channel_id: str,
        limit: int = 20,
    ) -> List[BusMessage]:
        """
        Get the MOST RECENT ``limit`` messages of a channel, oldest→newest.

        The recent-scrollback complement to ``get_messages`` (which returns
        the OLDEST ``limit``). Used to give a triggered team-room agent the
        recent conversation as context, not just the message that
        @mentioned it.

        Args:
            channel_id: The channel to fetch messages from.
            limit: Maximum number of messages to return.

        Returns:
            List of BusMessage — the newest ``limit``, reordered ASC.
        """
        ...

    @abstractmethod
    async def get_unread(self, agent_id: str) -> List[BusMessage]:
        """
        Get all unread messages for an agent across all channels.

        Args:
            agent_id: The agent to fetch unread messages for.

        Returns:
            List of BusMessage that the agent has not yet read.
        """
        ...

    @abstractmethod
    async def mark_read(self, agent_id: str, message_ids: List[str]) -> None:
        """
        Mark messages as read by advancing the read cursor.

        Args:
            agent_id: The agent marking messages as read.
            message_ids: List of message IDs to mark as read.
        """
        ...

    @abstractmethod
    async def send_to_agent(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        msg_type: str = "text",
        attachments: Optional[List[dict]] = None,
    ) -> str:
        """
        Send a message directly to another agent by agent_id.

        Auto-creates a direct channel between the two agents if one
        doesn't already exist.

        Args:
            from_agent: The agent ID of the sender.
            to_agent: The agent ID of the recipient.
            content: The message content.
            msg_type: The message type (default: "text").
            attachments: Optional list of bus-attachment dicts (see
                _bus_attachment_impl); files travel by reference, not bytes.

        Returns:
            The generated message_id.
        """
        ...

    # ===== Channel Management =====

    @abstractmethod
    async def create_channel(
        self,
        name: str,
        members: List[str],
        channel_type: str = "group",
    ) -> str:
        """
        Create a new channel with the given members.

        Args:
            name: Human-readable channel name.
            members: List of agent IDs to add as initial members.
            channel_type: "group" or "direct".

        Returns:
            The generated channel_id.
        """
        ...

    @abstractmethod
    async def join_channel(self, agent_id: str, channel_id: str) -> None:
        """
        Add an agent to a channel.

        Args:
            agent_id: The agent joining the channel.
            channel_id: The channel to join.
        """
        ...

    @abstractmethod
    async def leave_channel(self, agent_id: str, channel_id: str) -> None:
        """
        Remove an agent from a channel.

        Args:
            agent_id: The agent leaving the channel.
            channel_id: The channel to leave.
        """
        ...

    # ===== Agent Discovery =====

    @abstractmethod
    async def register_agent(
        self,
        agent_id: str,
        owner_user_id: str,
        capabilities: List[str],
        description: str,
        visibility: str = "private",
    ) -> None:
        """
        Register or update an agent in the discovery registry.

        Args:
            agent_id: Unique agent identifier.
            owner_user_id: The user who owns this agent.
            capabilities: List of capability tags.
            description: Human-readable description.
            visibility: "public" or "private".
        """
        ...

    @abstractmethod
    async def search_agents(
        self,
        query: str,
        requester_agent_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[BusAgentInfo]:
        """
        Search for agents by capability or description.

        Scoped to the requester's own user when ``requester_agent_id`` is
        given — cross-user discovery via the bus is disabled.

        Args:
            query: Search string matched against capabilities and description.
            requester_agent_id: The searching agent; results are limited to
                agents owned by the same user.
            limit: Maximum number of results.

        Returns:
            List of matching BusAgentInfo.
        """
        ...

    # ===== Delivery (for trigger/poller integration) =====

    @abstractmethod
    async def get_pending_messages(
        self,
        agent_id: str,
        limit: int = 50,
    ) -> List[BusMessage]:
        """
        Get messages that have not been processed by the agent.

        Uses the cursor model: returns messages with created_at after
        the agent's last_processed_at, excluding self-sent messages
        and poison messages (failure_count >= 3).

        Args:
            agent_id: The agent to fetch pending messages for.
            limit: Maximum number of messages to return.

        Returns:
            List of unprocessed BusMessage.
        """
        ...

    @abstractmethod
    async def ack_processed(
        self,
        agent_id: str,
        channel_id: str,
        up_to_timestamp: str,
    ) -> None:
        """
        Acknowledge that messages up to a timestamp have been processed.

        Args:
            agent_id: The agent acknowledging processing.
            channel_id: The channel being acknowledged.
            up_to_timestamp: ISO 8601 timestamp of the latest processed message.
        """
        ...

    @abstractmethod
    async def record_failure(
        self,
        message_id: str,
        agent_id: str,
        error: str,
    ) -> None:
        """
        Record a delivery failure for a message.

        Args:
            message_id: The failed message ID.
            agent_id: The agent that failed to process it.
            error: Error description.
        """
        ...

    @abstractmethod
    async def get_failure_count(
        self,
        message_id: str,
        agent_id: str,
    ) -> int:
        """
        Get the number of delivery failures for a message/agent pair.

        Args:
            message_id: The message ID.
            agent_id: The agent ID.

        Returns:
            Number of recorded failures (0 if none).
        """
        ...

    # ===== Channel Membership & Agent Profile =====

    @abstractmethod
    async def get_channel_members(self, channel_id: str) -> List[BusChannelMember]:
        """Get all members of a channel."""
        ...

    @abstractmethod
    async def kick_member(self, channel_id: str, agent_id: str) -> None:
        """Remove a member from a channel."""
        ...

    @abstractmethod
    async def get_agent_profile(self, agent_id: str) -> Optional[BusAgentInfo]:
        """Get a single agent's profile from the registry."""
        ...
