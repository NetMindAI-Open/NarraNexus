"""
@file_name: instance_repository.py
@author: NetMind.AI
@date: 2025-12-24
@description: ModuleInstance Repository - Data access layer for Instance data

Responsibilities:
- CRUD operations for ModuleInstance
- Query by agent_id, user_id, module_class, and other conditions
"""

import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.utils import utc_now
from xyz_agent_context.schema.instance_schema import (
    ModuleInstanceRecord,
    InstanceStatus,
)


class InstanceRepository(BaseRepository[ModuleInstanceRecord]):
    """
    ModuleInstance Repository implementation

    Usage example:
        repo = InstanceRepository(db_client)

        # Get an Instance
        instance = await repo.get_by_instance_id("chat_a1b2c3d4")

        # Get by Agent
        instances = await repo.get_by_agent("agent_123")

        # Create an Instance
        await repo.create_instance(instance)
    """

    table_name = "module_instances"
    id_field = "instance_id"  # Use instance_id as the business primary key (not the auto-increment id)

    _json_fields = {"dependencies", "config", "state", "keywords"}

    # ===== Query Methods =====

    async def get_by_instance_id(self, instance_id: str) -> Optional[ModuleInstanceRecord]:
        """
        Get an Instance by instance_id

        Args:
            instance_id: Instance ID

        Returns:
            ModuleInstanceRecord or None
        """
        logger.debug(f"    → InstanceRepository.get_by_instance_id({instance_id})")
        return await self.find_one({"instance_id": instance_id})

    async def get_by_agent(
        self,
        agent_id: str,
        status: Optional[InstanceStatus] = None,
        module_class: Optional[str] = None,
        is_public: Optional[bool] = None
    ) -> List[ModuleInstanceRecord]:
        """
        Get all Instances for an Agent

        Args:
            agent_id: Agent ID
            status: Optional, filter by status
            module_class: Optional, filter by Module type
            is_public: Optional, filter by public status

        Returns:
            List of ModuleInstanceRecord
        """
        logger.debug(f"    → InstanceRepository.get_by_agent({agent_id})")

        filters = {"agent_id": agent_id}
        if status:
            filters["status"] = status.value if isinstance(status, InstanceStatus) else status
        if module_class:
            filters["module_class"] = module_class
        if is_public is not None:
            filters["is_public"] = 1 if is_public else 0

        return await self.find(filters=filters, order_by="created_at DESC")

    async def get_by_agent_and_user(
        self,
        agent_id: str,
        user_id: str,
        include_public: bool = True
    ) -> List[ModuleInstanceRecord]:
        """
        Get all Instances accessible by an Agent and User

        Args:
            agent_id: Agent ID
            user_id: User ID
            include_public: Whether to include public instances

        Returns:
            List of ModuleInstanceRecord
        """
        logger.debug(f"    → InstanceRepository.get_by_agent_and_user({agent_id}, {user_id})")

        if include_public:
            # Get public or user-owned instances
            query = f"""
                SELECT * FROM {self.table_name}
                WHERE agent_id = %s AND (is_public = 1 OR user_id = %s)
                ORDER BY created_at DESC
            """
            rows = await self._db.execute(query, params=(agent_id, user_id))
        else:
            # Only get instances belonging to this user
            query = f"""
                SELECT * FROM {self.table_name}
                WHERE agent_id = %s AND user_id = %s
                ORDER BY created_at DESC
            """
            rows = await self._db.execute(query, params=(agent_id, user_id))

        return [self._row_to_entity(row) for row in rows] if rows else []

    async def get_public_instances(
        self,
        agent_id: str,
        module_class: Optional[str] = None
    ) -> List[ModuleInstanceRecord]:
        """
        Get all public Instances for an Agent

        Args:
            agent_id: Agent ID
            module_class: Optional, filter by Module type

        Returns:
            List of ModuleInstanceRecord
        """
        logger.debug(f"    → InstanceRepository.get_public_instances({agent_id})")

        filters = {"agent_id": agent_id, "is_public": 1}
        if module_class:
            filters["module_class"] = module_class

        return await self.find(filters=filters)

    async def get_chat_instances_by_user(
        self,
        agent_id: str,
        user_id: str,
        exclude_instance_ids: Optional[List[str]] = None
    ) -> List[ModuleInstanceRecord]:
        """
        Get all ChatModule instances for a user (2026-01-21 P1-2 dual-track memory loading)

        Used for short-term memory loading: get ChatModule instances for the user
        across all Narratives, excluding instances from the current Narrative
        (which belong to long-term memory).

        Args:
            agent_id: Agent ID
            user_id: User ID
            exclude_instance_ids: List of instance IDs to exclude (typically from the current Narrative)

        Returns:
            List of ModuleInstanceRecord (sorted by last_used_at descending)
        """
        logger.debug(f"    → InstanceRepository.get_chat_instances_by_user({agent_id}, {user_id})")

        # Query all ChatModule instances for this user
        query = f"""
            SELECT * FROM {self.table_name}
            WHERE agent_id = %s
              AND user_id = %s
              AND module_class = 'ChatModule'
              AND status = 'active'
            ORDER BY last_used_at DESC
        """
        rows = await self._db.execute(query, params=(agent_id, user_id), fetch=True)

        if not rows:
            return []

        instances = [self._row_to_entity(row) for row in rows]

        # Exclude specified instance IDs
        if exclude_instance_ids:
            instances = [
                inst for inst in instances
                if inst.instance_id not in exclude_instance_ids
            ]

        logger.debug(f"    ← InstanceRepository.get_chat_instances_by_user: {len(instances)} found")
        return instances

    # ===== Create and Update Methods =====

    async def create_instance(self, instance: ModuleInstanceRecord) -> int:
        """
        Create a new Instance

        Args:
            instance: ModuleInstanceRecord object

        Returns:
            Inserted record ID
        """
        logger.debug(f"    → InstanceRepository.create_instance({instance.instance_id})")
        return await self.insert(instance)

    async def update_status(
        self,
        instance_id: str,
        status: InstanceStatus,
        completed_at: Optional[datetime] = None
    ) -> int:
        """
        Update Instance status

        Args:
            instance_id: Instance ID
            status: New status
            completed_at: Completion time (optional)

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InstanceRepository.update_status({instance_id}, {status})")

        updates = {"status": status.value if isinstance(status, InstanceStatus) else status}
        if completed_at:
            updates["completed_at"] = completed_at.strftime('%Y-%m-%d %H:%M:%S')

        return await self.update(instance_id, updates)

    async def update_state(
        self,
        instance_id: str,
        state: Dict[str, Any]
    ) -> int:
        """
        Update Instance runtime state

        Args:
            instance_id: Instance ID
            state: New state data

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InstanceRepository.update_state({instance_id})")
        return await self.update(instance_id, {"state": json.dumps(state, ensure_ascii=False)})

    async def update_last_used(self, instance_id: str) -> int:
        """
        Update last used time

        Args:
            instance_id: Instance ID

        Returns:
            Number of affected rows
        """
        now = utc_now().strftime('%Y-%m-%d %H:%M:%S')
        return await self.update(instance_id, {"last_used_at": now})

    async def archive_instance(self, instance_id: str) -> int:
        """
        Archive an Instance

        Args:
            instance_id: Instance ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InstanceRepository.archive_instance({instance_id})")

        now = utc_now().strftime('%Y-%m-%d %H:%M:%S')
        return await self.update(instance_id, {
            "status": InstanceStatus.ARCHIVED.value,
            "archived_at": now
        })

    # ===== Vector Search =====

    def _row_to_entity(self, row: Dict[str, Any]) -> ModuleInstanceRecord:
        """Convert a database row to a ModuleInstanceRecord object"""
        return ModuleInstanceRecord(
            id=row.get("id"),
            instance_id=row["instance_id"],
            module_class=row["module_class"],
            agent_id=row["agent_id"],
            user_id=row.get("user_id"),
            is_public=bool(row.get("is_public", 0)),
            status=row.get("status", "active"),
            description=row.get("description") or "",
            dependencies=self._parse_json_field(row.get("dependencies"), []),
            config=self._parse_json_field(row.get("config"), {}),
            state=self._parse_json_field(row.get("state"), None),
            keywords=self._parse_json_field(row.get("keywords"), []),
            topic_hint=row.get("topic_hint") or "",
            created_at=row.get("created_at"),
            last_used_at=row.get("last_used_at"),
            completed_at=row.get("completed_at"),
            archived_at=row.get("archived_at"),
        )

    def _entity_to_row(self, entity: ModuleInstanceRecord) -> Dict[str, Any]:
        """Convert a ModuleInstanceRecord object to a database row"""
        return {
            "instance_id": entity.instance_id,
            "module_class": entity.module_class,
            "agent_id": entity.agent_id,
            "user_id": entity.user_id,
            "is_public": 1 if entity.is_public else 0,
            "status": entity.status if isinstance(entity.status, str) else entity.status.value,
            "description": entity.description,
            "dependencies": json.dumps(entity.dependencies, ensure_ascii=False),
            "config": json.dumps(entity.config, ensure_ascii=False),
            "state": json.dumps(entity.state, ensure_ascii=False) if entity.state else None,
            "keywords": json.dumps(entity.keywords, ensure_ascii=False),
            "topic_hint": entity.topic_hint,
            "created_at": entity.created_at.strftime('%Y-%m-%d %H:%M:%S') if entity.created_at else None,
            "last_used_at": entity.last_used_at.strftime('%Y-%m-%d %H:%M:%S') if entity.last_used_at else None,
            "completed_at": entity.completed_at.strftime('%Y-%m-%d %H:%M:%S') if entity.completed_at else None,
            "archived_at": entity.archived_at.strftime('%Y-%m-%d %H:%M:%S') if entity.archived_at else None,
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
