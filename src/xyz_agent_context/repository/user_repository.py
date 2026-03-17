"""
@file_name: user_repository.py
@author: NetMind.AI
@date: 2025-12-02
@description: User Repository - Data access layer for user data

Responsibilities:
- CRUD operations for User
- User status management
- Login time updates
"""

import json
from datetime import datetime, timezone as dt_timezone
from typing import List, Dict, Any, Optional
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.schema import User, UserStatus


class UserRepository(BaseRepository[User]):
    """
    User Repository implementation

    Usage example:
        repo = UserRepository(db_client)

        # Get a user
        user = await repo.get_user("user_123")

        # Add a user
        await repo.add_user(user_id, user_type, display_name)

        # Update login time
        await repo.update_last_login("user_123")
    """

    table_name = "users"
    id_field = "id"

    _json_fields = {"metadata"}

    async def get_user(self, user_id: str) -> Optional[User]:
        """Get a user (case-sensitive)"""
        logger.debug(f"    → UserRepository.get_user({user_id})")
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} WHERE BINARY user_id = %s LIMIT 1",
            params=(user_id,),
            fetch=True,
        )
        if rows:
            return self._row_to_entity(rows[0])
        return None

    async def add_user(
        self,
        user_id: str,
        user_type: str,
        display_name: Optional[str] = None,
        email: Optional[str] = None,
        phone_number: Optional[str] = None,
        nickname: Optional[str] = None,
        timezone: str = "UTC",
        status: UserStatus = UserStatus.ACTIVE,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Add a new user"""
        logger.debug(f"    → UserRepository.add_user({user_id})")

        user = User(
            user_id=user_id,
            user_type=user_type,
            display_name=display_name,
            email=email,
            phone_number=phone_number,
            nickname=nickname,
            timezone=timezone,
            status=status,
            metadata=metadata,
        )

        return await self.insert(user)

    async def update_user(self, user_id: str, updates: Dict[str, Any]) -> int:
        """Update user information"""
        logger.debug(f"    → UserRepository.update_user({user_id})")

        # Serialize JSON fields
        if "metadata" in updates and not isinstance(updates["metadata"], str):
            updates["metadata"] = json.dumps(updates["metadata"], ensure_ascii=False)

        # Handle enum types
        if "status" in updates and isinstance(updates["status"], UserStatus):
            updates["status"] = updates["status"].value

        query = f"""
            UPDATE {self.table_name}
            SET {', '.join(f'`{k}` = %s' for k in updates.keys())}
            WHERE BINARY user_id = %s
        """

        params = list(updates.values()) + [user_id]
        result = await self._db.execute(query, params=tuple(params), fetch=False)
        return result if isinstance(result, int) else 0

    async def update_last_login(self, user_id: str) -> int:
        """Update last login time"""
        logger.debug(f"    → UserRepository.update_last_login({user_id})")

        return await self.update_user(user_id, {
            "last_login_time": datetime.now(dt_timezone.utc)
        })

    async def update_timezone(self, user_id: str, timezone: str) -> int:
        """
        Update user timezone

        Args:
            user_id: User ID
            timezone: IANA timezone string (e.g. 'Asia/Shanghai')

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → UserRepository.update_timezone({user_id}, {timezone})")

        return await self.update_user(user_id, {"timezone": timezone})

    async def get_user_timezone(self, user_id: str) -> str:
        """
        Get user timezone

        Args:
            user_id: User ID

        Returns:
            User timezone string, returns 'UTC' if user does not exist
        """
        user = await self.get_user(user_id)
        if user:
            return user.timezone
        return "UTC"

    async def delete_user(self, user_id: str, soft_delete: bool = True) -> int:
        """Delete a user (soft delete by default)"""
        logger.debug(f"    → UserRepository.delete_user({user_id}, soft={soft_delete})")

        if soft_delete:
            return await self.update_user(user_id, {"status": UserStatus.DELETED.value})
        else:
            query = f"DELETE FROM {self.table_name} WHERE BINARY user_id = %s"
            result = await self._db.execute(query, params=(user_id,), fetch=False)
            return result if isinstance(result, int) else 0

    async def list_users(
        self,
        user_type: Optional[str] = None,
        status: Optional[UserStatus] = None,
        limit: int = 100
    ) -> List[User]:
        """List users"""
        logger.debug("    → UserRepository.list_users()")

        filters = {}
        if user_type:
            filters["user_type"] = user_type
        if status:
            filters["status"] = status.value

        return await self.find(
            filters=filters if filters else {},
            limit=limit,
            order_by="create_time DESC"
        )

    def _row_to_entity(self, row: Dict[str, Any]) -> User:
        """Convert a database row to a User object"""
        metadata = self._parse_json_field(row.get("metadata"), None)

        return User(
            id=row.get("id"),
            user_id=row["user_id"],
            user_type=row["user_type"],
            display_name=row.get("display_name"),
            email=row.get("email"),
            phone_number=row.get("phone_number"),
            nickname=row.get("nickname"),
            timezone=row.get("timezone", "UTC"),
            status=UserStatus(row.get("status", "active")),
            metadata=metadata,
            last_login_time=row.get("last_login_time"),
            create_time=row.get("create_time"),
            update_time=row.get("update_time"),
        )

    def _entity_to_row(self, entity: User) -> Dict[str, Any]:
        """Convert a User object to a database row"""
        return {
            "user_id": entity.user_id,
            "user_type": entity.user_type,
            "display_name": entity.display_name,
            "email": entity.email,
            "phone_number": entity.phone_number,
            "nickname": entity.nickname,
            "timezone": entity.timezone,
            "status": entity.status.value,
            "metadata": json.dumps(entity.metadata, ensure_ascii=False) if entity.metadata else None,
            "last_login_time": entity.last_login_time,
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
