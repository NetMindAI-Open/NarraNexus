"""
@file_name: agent_message_repository.py
@author: NetMind.AI
@date: 2025-12-10
@description: Agent Message Repository - Data access layer for Agent message lists

Responsibilities:
- CRUD operations for Agent messages
- Retrieve message lists by Agent
- Retrieve unresponded messages
- Update message response status
"""

from typing import List, Dict, Any, Optional
from uuid import uuid4
from loguru import logger

from .base import BaseRepository
from narranexus.platform.utils import utc_now
from narranexus.platform.schema.agent_message_schema import (
    AgentMessage,
    MessageSourceType,
)


class AgentMessageRepository(BaseRepository[AgentMessage]):
    """
    Agent Message Repository implementation

    Usage example:
        repo = AgentMessageRepository(db_client)

        # Create a message
        msg_id = await repo.create_message(
            agent_id="agent_123",
            source_type=MessageSourceType.USER,
            source_id="user_456",
            content="Hello"
        )

        # Get message list for an Agent
        messages = await repo.get_messages(agent_id="agent_123")

        # Get unresponded messages
        unresponded = await repo.get_unresponded_messages(agent_id="agent_123")

        # Update message response status
        await repo.update_response_status(
            message_id="amsg_xxx",
            narrative_id="narr_123",
            event_id="evt_456"
        )
    """

    table_name = "agent_messages"
    id_field = "id"

    # =========================================================================
    # Create Message
    # =========================================================================

    async def create_message(
        self,
        agent_id: str,
        source_type: MessageSourceType,
        source_id: str,
        content: str,
        if_response: bool = False,
        narrative_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> str:
        """
        Create an Agent message

        Args:
            agent_id: Agent ID (the Agent this message belongs to)
            source_type: Message source type
            source_id: Source ID
            content: Message content
            if_response: Whether the message has been responded to
            narrative_id: Associated narrative ID
            event_id: Associated event ID

        Returns:
            The created message_id
        """
        logger.debug(f"    → AgentMessageRepository.create_message(agent={agent_id}, source={source_type.value})")

        message_id = f"amsg_{uuid4().hex[:12]}"

        message = AgentMessage(
            message_id=message_id,
            agent_id=agent_id,
            source_type=source_type,
            source_id=source_id,
            content=content,
            if_response=if_response,
            narrative_id=narrative_id,
            event_id=event_id,
            created_at=utc_now(),
        )

        await self.insert(message)
        return message_id

    # =========================================================================
    # Query Messages
    # =========================================================================

    async def get_message(self, message_id: str) -> Optional[AgentMessage]:
        """
        Get a single message

        Args:
            message_id: Message ID

        Returns:
            AgentMessage or None
        """
        logger.debug(f"    → AgentMessageRepository.get_message({message_id})")
        return await self.find_one({"message_id": message_id})

    async def get_messages(
        self,
        agent_id: str,
        source_type: Optional[MessageSourceType] = None,
        if_response: Optional[bool] = None,
        limit: int = 50,
        order_by: str = "created_at DESC"
    ) -> List[AgentMessage]:
        """
        Get message list for an Agent

        Args:
            agent_id: Agent ID
            source_type: Filter by source type
            if_response: Filter by response status
            limit: Maximum number of results
            order_by: Sort field

        Returns:
            List of AgentMessage
        """
        logger.debug(f"    → AgentMessageRepository.get_messages(agent={agent_id})")

        filters: Dict[str, Any] = {"agent_id": agent_id}

        if source_type is not None:
            filters["source_type"] = source_type.value

        if if_response is not None:
            filters["if_response"] = if_response

        return await self.find(
            filters=filters,
            limit=limit,
            order_by=order_by
        )

    async def get_unresponded_messages(
        self,
        agent_id: str,
        limit: int = 50
    ) -> List[AgentMessage]:
        """
        Get unresponded messages for an Agent

        Args:
            agent_id: Agent ID
            limit: Maximum number of results

        Returns:
            List of unresponded AgentMessage (ascending by time, FIFO)
        """
        logger.debug(f"    → AgentMessageRepository.get_unresponded_messages(agent={agent_id})")

        return await self.get_messages(
            agent_id=agent_id,
            if_response=False,
            limit=limit,
            order_by="created_at ASC"  # FIFO (First In, First Out)
        )

    async def get_messages_by_source(
        self,
        agent_id: str,
        source_type: MessageSourceType,
        source_id: str,
        limit: int = 50
    ) -> List[AgentMessage]:
        """
        Get messages by source

        Args:
            agent_id: Agent ID
            source_type: Source type
            source_id: Source ID
            limit: Maximum number of results

        Returns:
            List of AgentMessage
        """
        logger.debug(f"    → AgentMessageRepository.get_messages_by_source(agent={agent_id}, source={source_id})")

        return await self.find(
            filters={
                "agent_id": agent_id,
                "source_type": source_type.value,
                "source_id": source_id,
            },
            limit=limit,
            order_by="created_at DESC"
        )

    # =========================================================================
    # Update Messages
    # =========================================================================

    async def update_response_status(
        self,
        message_id: str,
        narrative_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> int:
        """
        Update the response status of a message

        Args:
            message_id: Message ID
            narrative_id: Associated narrative ID
            event_id: Associated event ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → AgentMessageRepository.update_response_status({message_id})")

        update_data: Dict[str, Any] = {"if_response": True}

        if narrative_id is not None:
            update_data["narrative_id"] = narrative_id

        if event_id is not None:
            update_data["event_id"] = event_id

        return await self.update(message_id, update_data)

    async def batch_update_response_status(
        self,
        message_ids: List[str],
        narrative_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> int:
        """
        Batch update message response status

        Args:
            message_ids: List of message IDs
            narrative_id: Associated narrative ID
            event_id: Associated event ID

        Returns:
            Number of affected rows
        """
        if not message_ids:
            return 0

        logger.debug(f"    → AgentMessageRepository.batch_update_response_status({len(message_ids)} messages)")

        # Build update data
        set_clauses = ["if_response = TRUE"]
        params: List[Any] = []

        if narrative_id is not None:
            set_clauses.append("narrative_id = %s")
            params.append(narrative_id)

        if event_id is not None:
            set_clauses.append("event_id = %s")
            params.append(event_id)

        # Build IN clause
        placeholders = ", ".join(["%s"] * len(message_ids))
        params.extend(message_ids)

        query = f"""
            UPDATE {self.table_name}
            SET {", ".join(set_clauses)}
            WHERE message_id IN ({placeholders})
        """

        result = await self._db.execute(query, params=tuple(params), fetch=False)
        return result if isinstance(result, int) else 0

    # =========================================================================
    # Delete Messages
    # =========================================================================

    async def delete_message(self, message_id: str) -> int:
        """
        Delete a message

        Args:
            message_id: Message ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → AgentMessageRepository.delete_message({message_id})")

        query = f"DELETE FROM {self.table_name} WHERE message_id = %s"
        result = await self._db.execute(query, params=(message_id,), fetch=False)
        return result if isinstance(result, int) else 0

    async def delete_agent_messages(self, agent_id: str) -> int:
        """
        Delete all messages for an Agent

        Args:
            agent_id: Agent ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → AgentMessageRepository.delete_agent_messages({agent_id})")

        query = f"DELETE FROM {self.table_name} WHERE agent_id = %s"
        result = await self._db.execute(query, params=(agent_id,), fetch=False)
        return result if isinstance(result, int) else 0

    # =========================================================================
    # Conversion Methods
    # =========================================================================

    def _row_to_entity(self, row: Dict[str, Any]) -> AgentMessage:
        """
        Convert a database row to an AgentMessage object
        """
        return AgentMessage(
            id=row.get("id"),
            message_id=row["message_id"],
            agent_id=row["agent_id"],
            source_type=MessageSourceType(row["source_type"]),
            source_id=row["source_id"],
            content=row.get("content", ""),
            if_response=row.get("if_response", False),
            narrative_id=row.get("narrative_id"),
            event_id=row.get("event_id"),
            created_at=row.get("created_at"),
        )

    def _entity_to_row(self, entity: AgentMessage) -> Dict[str, Any]:
        """
        Convert an AgentMessage object to a database row
        """
        return {
            "message_id": entity.message_id,
            "agent_id": entity.agent_id,
            "source_type": entity.source_type.value,
            "source_id": entity.source_id,
            "content": entity.content,
            "if_response": entity.if_response,
            "narrative_id": entity.narrative_id,
            "event_id": entity.event_id,
            "created_at": entity.created_at,
        }
