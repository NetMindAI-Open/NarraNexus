"""
@file_name: inbox_repository.py
@author: NetMind.AI
@date: 2025-12-02
@description: Inbox Repository - Data access layer for inbox messages

Responsibilities:
- CRUD operations for Inbox messages
- Unread message count
- Mark as read
- Filter by source type
"""

import json
from typing import List, Dict, Any, Optional
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.utils import utc_now
from xyz_agent_context.schema.inbox_schema import (
    InboxMessage,
    InboxMessageType,
    MessageSource,
)


class InboxRepository(BaseRepository[InboxMessage]):
    """
    Inbox Repository implementation

    Usage example:
        repo = InboxRepository(db_client)

        # Create a message
        msg_id = await repo.create_message(user_id, title, content)

        # Get user messages
        messages = await repo.get_messages(user_id)

        # Get unread count
        count = await repo.get_unread_count(user_id)

        # Mark as read
        await repo.mark_as_read(message_id)
    """

    table_name = "inbox_table"
    id_field = "id"

    # JSON fields
    _json_fields = {"source"}

    # =========================================================================
    # Basic CRUD
    # =========================================================================

    async def create_message(
        self,
        user_id: str,
        title: str,
        content: str,
        message_id: str,
        message_type: InboxMessageType = InboxMessageType.JOB_RESULT,
        source: Optional[MessageSource] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        event_id: Optional[str] = None
    ) -> int:
        """
        Create an Inbox message

        Args:
            user_id: User ID
            title: Message title
            content: Message content
            message_id: Message ID
            message_type: Message type
            source: Message source
            source_type: Source type
            source_id: Source ID
            event_id: Associated event ID

        Returns:
            Inserted record ID
        """
        logger.debug(f"    → InboxRepository.create_message({user_id})")

        # Build source
        if source is None and source_type and source_id:
            source = MessageSource(type=source_type, id=source_id)

        message = InboxMessage(
            message_id=message_id,
            user_id=user_id,
            source=source,
            event_id=event_id,
            message_type=message_type,
            title=title,
            content=content,
            is_read=False,
            created_at=utc_now(),
        )

        return await self.insert(message)

    async def get_message(self, message_id: str) -> Optional[InboxMessage]:
        """
        Get a single message

        Args:
            message_id: Message ID

        Returns:
            InboxMessage or None
        """
        logger.debug(f"    → InboxRepository.get_message({message_id})")
        return await self.find_one({"message_id": message_id})

    async def get_messages(
        self,
        user_id: str,
        is_read: Optional[bool] = None,
        message_type: Optional[InboxMessageType] = None,
        source_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[InboxMessage]:
        """
        Get user message list

        Args:
            user_id: User ID
            is_read: Filter by read status
            message_type: Filter by message type
            source_type: Filter by source type
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of InboxMessage
        """
        logger.debug(f"    → InboxRepository.get_messages({user_id})")

        # If filtering by source_type, use raw SQL
        if source_type:
            query = f"""
                SELECT * FROM {self.table_name}
                WHERE user_id = %s
                AND JSON_EXTRACT(source, '$.type') = %s
            """
            params = [user_id, source_type]

            if is_read is not None:
                query += " AND is_read = %s"
                params.append(is_read)
            if message_type:
                query += " AND message_type = %s"
                params.append(message_type.value)

            query += f" ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}"

            results = await self._db.execute(query, params=tuple(params), fetch=True)
            return [self._row_to_entity(row) for row in results]

        # Otherwise use standard query
        filters = {"user_id": user_id}
        if is_read is not None:
            filters["is_read"] = is_read
        if message_type:
            filters["message_type"] = message_type.value

        return await self.find(
            filters=filters,
            limit=limit,
            offset=offset,
            order_by="created_at DESC"
        )

    async def get_total_count(
        self,
        user_id: str,
        is_read: Optional[bool] = None,
    ) -> int:
        """
        Get total message count for a user

        Args:
            user_id: User ID
            is_read: Filter by read status

        Returns:
            Total message count
        """
        query = f"SELECT COUNT(*) as cnt FROM {self.table_name} WHERE user_id = %s"
        params: list = [user_id]

        if is_read is not None:
            query += " AND is_read = %s"
            params.append(is_read)

        results = await self._db.execute(query, params=tuple(params), fetch=True)
        return results[0]["cnt"] if results else 0

    async def get_unread_count(self, user_id: str) -> int:
        """
        Get unread message count

        Args:
            user_id: User ID

        Returns:
            Number of unread messages
        """
        logger.debug(f"    → InboxRepository.get_unread_count({user_id})")

        query = f"""
            SELECT COUNT(*) as count FROM {self.table_name}
            WHERE user_id = %s AND is_read = FALSE
        """

        results = await self._db.execute(query, params=(user_id,), fetch=True)
        return results[0]["count"] if results else 0

    async def mark_as_read(self, message_id: str) -> int:
        """
        Mark a message as read

        Args:
            message_id: Message ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InboxRepository.mark_as_read({message_id})")

        query = f"""
            UPDATE {self.table_name}
            SET is_read = TRUE
            WHERE message_id = %s
        """

        result = await self._db.execute(query, params=(message_id,), fetch=False)
        return result if isinstance(result, int) else 0

    async def mark_all_as_read(self, user_id: str) -> int:
        """
        Mark all messages as read for a user

        Args:
            user_id: User ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InboxRepository.mark_all_as_read({user_id})")

        query = f"""
            UPDATE {self.table_name}
            SET is_read = TRUE
            WHERE user_id = %s AND is_read = FALSE
        """

        result = await self._db.execute(query, params=(user_id,), fetch=False)
        return result if isinstance(result, int) else 0

    async def delete_message(self, message_id: str) -> int:
        """
        Delete a message

        Args:
            message_id: Message ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InboxRepository.delete_message({message_id})")

        query = f"DELETE FROM {self.table_name} WHERE message_id = %s"
        result = await self._db.execute(query, params=(message_id,), fetch=False)
        return result if isinstance(result, int) else 0

    async def delete_user_messages(self, user_id: str) -> int:
        """
        Delete all messages for a user

        Args:
            user_id: User ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InboxRepository.delete_user_messages({user_id})")

        query = f"DELETE FROM {self.table_name} WHERE user_id = %s"
        result = await self._db.execute(query, params=(user_id,), fetch=False)
        return result if isinstance(result, int) else 0

    # =========================================================================
    # Conversion Methods
    # =========================================================================

    def _row_to_entity(self, row: Dict[str, Any]) -> InboxMessage:
        """
        Convert a database row to an InboxMessage object
        """
        # Parse source JSON
        source_data = self._parse_json_field(row.get("source"), None)
        source = MessageSource(**source_data) if source_data else None

        return InboxMessage(
            id=row.get("id"),
            message_id=row["message_id"],
            user_id=row["user_id"],
            source=source,
            event_id=row.get("event_id"),
            message_type=InboxMessageType(row["message_type"]),
            title=row["title"],
            content=row.get("content", ""),
            is_read=row.get("is_read", False),
            created_at=row.get("created_at"),
        )

    def _entity_to_row(self, entity: InboxMessage) -> Dict[str, Any]:
        """
        Convert an InboxMessage object to a database row
        """
        source_json = None
        if entity.source:
            source_json = json.dumps(entity.source.model_dump(), ensure_ascii=False)

        return {
            "message_id": entity.message_id,
            "user_id": entity.user_id,
            "source": source_json,
            "event_id": entity.event_id,
            "message_type": entity.message_type.value,
            "title": entity.title,
            "content": entity.content,
            "is_read": entity.is_read,
            "created_at": entity.created_at,
        }

    @staticmethod
    def _parse_json_field(value: Any, default: Any) -> Any:
        """Parse a JSON field"""
        if value is None:
            return default

        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default

        return value
