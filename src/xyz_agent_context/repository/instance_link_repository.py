"""
@file_name: instance_link_repository.py
@author: NetMind.AI
@date: 2025-12-24
@description: Instance-Narrative Link Repository - Manages many-to-many relationships between Instances and Narratives

Responsibilities:
- Create/remove links between Instances and Narratives
- Query Narratives linked to an Instance
- Query Instances linked to a Narrative
"""

from typing import List, Dict, Any, Optional
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.utils import utc_now
from xyz_agent_context.schema.instance_schema import (
    InstanceNarrativeLink,
    LinkType,
    InstanceStatus,
)


class InstanceNarrativeLinkRepository(BaseRepository[InstanceNarrativeLink]):
    """
    Instance-Narrative Link Repository implementation

    Usage example:
        repo = InstanceNarrativeLinkRepository(db_client)

        # Create a link
        await repo.link("chat_a1b2", "nar_x1y2")

        # Remove a link
        await repo.unlink("chat_a1b2", "nar_x1y2")

        # Get all Instances for a Narrative
        instance_ids = await repo.get_instances_for_narrative("nar_x1y2")

        # Get all Narratives linked to an Instance
        narrative_ids = await repo.get_narratives_for_instance("chat_a1b2")
    """

    table_name = "instance_narrative_links"
    id_field = "id"

    _json_fields = set()

    # ===== Link Operations =====

    async def link(
        self,
        instance_id: str,
        narrative_id: str,
        link_type: LinkType = LinkType.ACTIVE,
        local_status: InstanceStatus = InstanceStatus.ACTIVE
    ) -> int:
        """
        Create a link between Instance and Narrative

        Args:
            instance_id: Instance ID
            narrative_id: Narrative ID
            link_type: Link type (active, history, shared)
            local_status: Status within this Narrative

        Returns:
            Inserted record ID, or 0 if already exists
        """
        logger.debug(f"    → InstanceLinkRepository.link({instance_id}, {narrative_id})")

        # Check if already exists
        existing = await self.find_one({
            "instance_id": instance_id,
            "narrative_id": narrative_id
        })

        if existing:
            # Already exists, update link_type
            if existing.link_type != link_type.value:
                await self._update_link_type(instance_id, narrative_id, link_type)
            return 0

        # Create new link
        link = InstanceNarrativeLink(
            instance_id=instance_id,
            narrative_id=narrative_id,
            link_type=link_type,
            local_status=local_status,
            linked_at=utc_now(),
        )

        try:
            return await self.insert(link)
        except Exception as e:  # noqa: BLE001 — re-raised unless it's THIS race
            # Race: the find_one above said "not linked", but a concurrent run
            # (e.g. the JobTrigger poller and a chat turn both syncing the same
            # agent↔narrative — common once an agent has many jobs) inserted the
            # same (instance_id, narrative_id) pair before us, tripping the
            # composite UNIQUE constraint. That is exactly the state we wanted —
            # the link now exists — so treat it as a no-op (return 0). Anything
            # that is NOT this duplicate-key collision is re-raised untouched.
            msg = str(e).lower()
            if "unique constraint failed" in msg or "duplicate entry" in msg:
                logger.debug(
                    f"    link({instance_id}, {narrative_id}) lost an insert race "
                    f"— link already created concurrently; treating as existing."
                )
                return 0
            raise

    async def unlink(
        self,
        instance_id: str,
        narrative_id: str,
        to_history: bool = True
    ) -> int:
        """
        Remove the link between Instance and Narrative

        Args:
            instance_id: Instance ID
            narrative_id: Narrative ID
            to_history: If True, change link_type to history; otherwise delete the record

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → InstanceLinkRepository.unlink({instance_id}, {narrative_id})")

        if to_history:
            # Mark as history
            now = utc_now().strftime('%Y-%m-%d %H:%M:%S')
            query = f"""
                UPDATE {self.table_name}
                SET link_type = 'history', unlinked_at = %s
                WHERE instance_id = %s AND narrative_id = %s
            """
            result = await self._db.execute(
                query,
                params=(now, instance_id, narrative_id),
                fetch=False
            )
            return result if isinstance(result, int) else 0
        else:
            # Delete directly
            query = f"""
                DELETE FROM {self.table_name}
                WHERE instance_id = %s AND narrative_id = %s
            """
            result = await self._db.execute(
                query,
                params=(instance_id, narrative_id),
                fetch=False
            )
            return result if isinstance(result, int) else 0

    async def _update_link_type(
        self,
        instance_id: str,
        narrative_id: str,
        link_type: LinkType
    ) -> int:
        """Update link type"""
        query = f"""
            UPDATE {self.table_name}
            SET link_type = %s
            WHERE instance_id = %s AND narrative_id = %s
        """
        result = await self._db.execute(
            query,
            params=(link_type.value, instance_id, narrative_id),
            fetch=False
        )
        return result if isinstance(result, int) else 0

    # ===== Query Methods =====

    async def get_instances_for_narrative(
        self,
        narrative_id: str,
        link_type: Optional[LinkType] = LinkType.ACTIVE
    ) -> List[str]:
        """
        Get all Instance IDs linked to a Narrative

        Args:
            narrative_id: Narrative ID
            link_type: Optional, filter by link type (defaults to active only)

        Returns:
            List of Instance IDs
        """
        logger.debug(f"    → InstanceLinkRepository.get_instances_for_narrative({narrative_id})")

        filters = {"narrative_id": narrative_id}
        if link_type:
            filters["link_type"] = link_type.value if isinstance(link_type, LinkType) else link_type

        links = await self.find(filters=filters)
        return [link.instance_id for link in links]

    async def get_narratives_for_instance(
        self,
        instance_id: str,
        link_type: Optional[LinkType] = None
    ) -> List[str]:
        """
        Get all Narrative IDs linked to an Instance

        Args:
            instance_id: Instance ID
            link_type: Optional, filter by link type

        Returns:
            List of Narrative IDs
        """
        logger.debug(f"    → InstanceLinkRepository.get_narratives_for_instance({instance_id})")

        filters = {"instance_id": instance_id}
        if link_type:
            filters["link_type"] = link_type.value if isinstance(link_type, LinkType) else link_type

        links = await self.find(filters=filters)
        return [link.narrative_id for link in links]

    async def get_active_links_for_narrative(
        self,
        narrative_id: str
    ) -> List[InstanceNarrativeLink]:
        """
        Get all active links for a Narrative

        Args:
            narrative_id: Narrative ID

        Returns:
            List of InstanceNarrativeLink
        """
        return await self.find(
            filters={"narrative_id": narrative_id, "link_type": "active"}
        )

    async def update_local_status(
        self,
        instance_id: str,
        narrative_id: str,
        local_status: InstanceStatus
    ) -> int:
        """
        Update the status of an Instance within a specific Narrative

        Args:
            instance_id: Instance ID
            narrative_id: Narrative ID
            local_status: New local status

        Returns:
            Number of affected rows
        """
        query = f"""
            UPDATE {self.table_name}
            SET local_status = %s
            WHERE instance_id = %s AND narrative_id = %s
        """
        result = await self._db.execute(
            query,
            params=(
                local_status.value if isinstance(local_status, InstanceStatus) else local_status,
                instance_id,
                narrative_id
            ),
            fetch=False
        )
        return result if isinstance(result, int) else 0

    async def is_linked(self, instance_id: str, narrative_id: str) -> bool:
        """
        Check if an Instance is linked to a Narrative

        Args:
            instance_id: Instance ID
            narrative_id: Narrative ID

        Returns:
            Whether they are linked
        """
        existing = await self.find_one({
            "instance_id": instance_id,
            "narrative_id": narrative_id,
            "link_type": "active"
        })
        return existing is not None

    # ===== Batch Operations =====

    async def link_multiple(
        self,
        instance_ids: List[str],
        narrative_id: str,
        link_type: LinkType = LinkType.ACTIVE
    ) -> int:
        """
        Batch create links

        Args:
            instance_ids: List of Instance IDs
            narrative_id: Narrative ID
            link_type: Link type

        Returns:
            Number of newly created links
        """
        count = 0
        for instance_id in instance_ids:
            result = await self.link(instance_id, narrative_id, link_type)
            if result > 0:
                count += 1
        return count

    async def unlink_all_for_narrative(
        self,
        narrative_id: str,
        to_history: bool = True
    ) -> int:
        """
        Remove all links for a Narrative

        Args:
            narrative_id: Narrative ID
            to_history: Whether to mark as history

        Returns:
            Number of affected rows
        """
        if to_history:
            now = utc_now().strftime('%Y-%m-%d %H:%M:%S')
            query = f"""
                UPDATE {self.table_name}
                SET link_type = 'history', unlinked_at = %s
                WHERE narrative_id = %s AND link_type = 'active'
            """
            result = await self._db.execute(
                query,
                params=(now, narrative_id),
                fetch=False
            )
        else:
            query = f"""
                DELETE FROM {self.table_name}
                WHERE narrative_id = %s
            """
            result = await self._db.execute(
                query,
                params=(narrative_id,),
                fetch=False
            )

        return result if isinstance(result, int) else 0

    # ===== Data Conversion =====

    def _row_to_entity(self, row: Dict[str, Any]) -> InstanceNarrativeLink:
        """Convert a database row to an InstanceNarrativeLink object"""
        return InstanceNarrativeLink(
            instance_id=row["instance_id"],
            narrative_id=row["narrative_id"],
            link_type=row.get("link_type", "active"),
            local_status=row.get("local_status", "active"),
            linked_at=row.get("linked_at"),
            unlinked_at=row.get("unlinked_at"),
        )

    def _entity_to_row(self, entity: InstanceNarrativeLink) -> Dict[str, Any]:
        """Convert an InstanceNarrativeLink object to a database row"""
        return {
            "instance_id": entity.instance_id,
            "narrative_id": entity.narrative_id,
            "link_type": entity.link_type if isinstance(entity.link_type, str) else entity.link_type.value,
            "local_status": entity.local_status if isinstance(entity.local_status, str) else entity.local_status.value,
            "linked_at": entity.linked_at.strftime('%Y-%m-%d %H:%M:%S') if entity.linked_at else None,
            "unlinked_at": entity.unlinked_at.strftime('%Y-%m-%d %H:%M:%S') if entity.unlinked_at else None,
        }
