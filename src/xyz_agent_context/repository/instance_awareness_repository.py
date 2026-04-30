"""
@file_name: instance_awareness_repository.py
@author: NetMind.AI
@date: 2025-12-24
@description: Instance Awareness Repository

Responsibilities:
- CRUD operations for the instance_awareness table
- Query Awareness data by instance_id
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional
from loguru import logger

from .base import BaseRepository


@dataclass
class InstanceAwareness:
    """
    Instance Awareness entity

    Attributes:
        instance_id: Instance ID (instance ID of the AwarenessModule)
        awareness: Awareness content (natural language description of Agent's self-awareness)
        id: Database auto-increment ID (optional)
        created_at: Creation time (optional)
        updated_at: Update time (optional)
    """
    instance_id: str
    awareness: str
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class InstanceAwarenessRepository(BaseRepository[InstanceAwareness]):
    """
    Instance Awareness Repository implementation

    Usage example:
        repo = InstanceAwarenessRepository(db_client)

        # Get Awareness
        awareness = await repo.get_by_instance("inst_xxx")

        # Update Awareness
        await repo.upsert("inst_xxx", "New awareness content")
    """

    table_name = "instance_awareness"
    id_field = "instance_id"

    async def get_by_instance(self, instance_id: str) -> Optional[InstanceAwareness]:
        """
        Get the Awareness for a specific Instance

        Args:
            instance_id: Instance ID

        Returns:
            InstanceAwareness object, or None if not found
        """
        logger.debug(f"    → InstanceAwarenessRepository.get_by_instance({instance_id})")
        return await self.find_one({"instance_id": instance_id})

    async def upsert(self, instance_id: str, awareness: str) -> bool:
        """
        Insert or update Awareness (UPSERT operation)

        Inserts if instance_id does not exist, updates if it does.

        Args:
            instance_id: Instance ID
            awareness: Awareness content

        Returns:
            Whether the operation was successful
        """
        logger.info(f"    → InstanceAwarenessRepository.upsert({instance_id})")
        logger.info(f"      → awareness content (first 100 chars): {awareness[:100] if awareness else 'None'}...")

        try:
            existing = await self.get_by_instance(instance_id)
            logger.info(f"      → existing record: {existing is not None}")

            if existing:
                # Update
                logger.info(f"      → existing awareness (first 100): {existing.awareness[:100] if existing.awareness else 'None'}...")
                rows_affected = await self.update(instance_id, {"awareness": awareness})
                logger.info(f"      → update rows_affected: {rows_affected}")
            else:
                # Insert
                logger.info(f"      → inserting new record for instance_id: {instance_id}")
                entity = InstanceAwareness(instance_id=instance_id, awareness=awareness)
                insert_id = await self.insert(entity)
                logger.info(f"      → insert_id: {insert_id}")
            return True
        except Exception as e:
            logger.exception(f"Failed to upsert awareness: {e}")
            import traceback
            logger.exception(traceback.format_exc())
            return False

    async def update_awareness(self, instance_id: str, awareness: str) -> int:
        """
        Update Awareness content

        Args:
            instance_id: Instance ID
            awareness: New Awareness content

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InstanceAwarenessRepository.update_awareness({instance_id})")
        return await self.update(instance_id, {"awareness": awareness})

    def _row_to_entity(self, row: Dict[str, Any]) -> InstanceAwareness:
        """Convert a database row to an InstanceAwareness object"""
        return InstanceAwareness(
            id=row.get("id"),
            instance_id=row["instance_id"],
            awareness=row.get("awareness", ""),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: InstanceAwareness) -> Dict[str, Any]:
        """Convert an InstanceAwareness object to a database row"""
        return {
            "instance_id": entity.instance_id,
            "awareness": entity.awareness,
        }
