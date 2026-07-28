"""
Instance management implementation

@file_name: instance_handler.py
@author: NetMind.AI
@date: 2025-12-22
@description: ModuleInstance dependency management and state transitions

Refactoring notes (2025-12-24):
- Instance status changes are written to the module_instances table
- Instance association changes are written to the instance_narrative_links table
- No longer operates on the narrative.active_instances JSON field
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING

from loguru import logger

from ..models import Narrative
from .crud import NarrativeCRUD

if TYPE_CHECKING:
    from xyz_agent_context.schema.module_schema import ModuleInstance, InstanceStatus
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient


class InstanceHandler:
    """
    Instance Manager

    Responsibilities:
    - Handle Instance completion events
    - Check dependencies
    - State transitions (BLOCKED -> ACTIVE)

    Refactoring notes:
    - Instance status changes are written to the module_instances table
    - Instance association changes are written to the instance_narrative_links table
    """

    def __init__(self, agent_id: str):
        """
        Initialize Instance Manager

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._crud = NarrativeCRUD(agent_id)
        self._db_client: Optional["AsyncDatabaseClient"] = None

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._crud.set_database_client(db_client)
        self._db_client = db_client

    async def _get_db_client(self) -> "AsyncDatabaseClient":
        """Get the database client"""
        if self._db_client is None:
            from xyz_agent_context.utils.db.db_factory import get_db_client
            self._db_client = await get_db_client()
        return self._db_client

    async def handle_completion(
        self,
        narrative_id: str,
        instance_id: str,
        new_status: "InstanceStatus",
        narrative: Optional[Narrative] = None,
        save_to_db: bool = True
    ) -> List[str]:
        """
        Handle Instance completion event

        Refactored workflow:
        1. Update instance status in the module_instances table
        2. Update instance_narrative_links table (mark as history)
        3. Check dependencies of other BLOCKED instances
        4. If dependencies are satisfied, transition BLOCKED -> ACTIVE

        Args:
            narrative_id: Narrative ID
            instance_id: Completed instance ID
            new_status: New status
            narrative: Narrative object (optional, for runtime cache update)
            save_to_db: Whether to save (deprecated, always saves to database)

        Returns:
            List of newly activated instance_ids
        """
        from xyz_agent_context.schema.module_schema import InstanceStatus
        from xyz_agent_context.repository import InstanceRepository, InstanceNarrativeLinkRepository
        from xyz_agent_context.schema.instance_schema import LinkType

        logger.info(f"Handling instance completion: {instance_id} → {new_status.value}")

        db_client = await self._get_db_client()
        instance_repo = InstanceRepository(db_client)
        link_repo = InstanceNarrativeLinkRepository(db_client)

        # 1. Get instance information
        db_instance = await instance_repo.get_by_instance_id(instance_id)
        if not db_instance:
            logger.warning(f"Instance {instance_id} not found in database")
            return []

        # 2. Update instance status (write to module_instances table)
        now = datetime.now(timezone.utc)
        await instance_repo.update_status(
            instance_id=instance_id,
            status=new_status,
            completed_at=now if new_status in [InstanceStatus.COMPLETED, InstanceStatus.FAILED] else None
        )
        logger.info(f"Updated instance status: {instance_id} → {new_status.value}")

        # 3. Update association status (write to instance_narrative_links table)
        # Mark the association as history
        await link_repo.unlink(instance_id, narrative_id, to_history=True)
        logger.info(f"Unlinked instance from narrative: {instance_id} ↔ {narrative_id}")

        # 4. Check dependencies of other BLOCKED instances
        # Get all active instances associated with the current narrative
        active_instance_ids = await link_repo.get_instances_for_narrative(
            narrative_id,
            link_type=LinkType.ACTIVE
        )

        # Get history-associated instance_ids (for dependency checking)
        history_instance_ids = await link_repo.get_instances_for_narrative(
            narrative_id,
            link_type=LinkType.HISTORY
        )
        # Ensure the just-completed instance is in the history list
        if instance_id not in history_instance_ids:
            history_instance_ids.append(instance_id)

        newly_activated = []

        for inst_id in active_instance_ids:
            inst = await instance_repo.get_by_instance_id(inst_id)
            if not inst:
                continue

            # Check if it is in BLOCKED status
            inst_status = inst.status if isinstance(inst.status, str) else inst.status.value
            if inst_status != InstanceStatus.BLOCKED.value and inst_status != "blocked":
                continue

            # Check dependencies
            dependencies = inst.dependencies or []
            all_deps_completed = self._check_dependencies_from_db(
                dependencies=dependencies,
                active_ids=active_instance_ids,
                history_ids=history_instance_ids
            )

            if all_deps_completed:
                # 1. Activate instance
                await instance_repo.update_status(inst_id, InstanceStatus.ACTIVE)
                newly_activated.append(inst_id)
                logger.info(f"Activated blocked instance: {inst_id}")

                # 2. If it's a JobModule, also set the Job's next_run_time
                if inst.module_class == "JobModule":
                    from xyz_agent_context.repository import JobRepository
                    job_repo = JobRepository(db_client)
                    updated = await job_repo.update_next_run_time_by_instance(
                        instance_id=inst_id,
                        next_run_time=datetime.now(timezone.utc)
                    )
                    if updated:
                        logger.info(f"Set next_run_time for Job (instance={inst_id})")

        # 5. Update runtime cache (if narrative object was provided)
        if narrative:
            # Remove the completed instance from active_instances
            narrative.active_instances = [
                inst for inst in narrative.active_instances
                if inst.instance_id != instance_id
            ]
            # Add to history
            if instance_id not in narrative.instance_history_ids:
                narrative.instance_history_ids.append(instance_id)

            # Update status of newly activated instances
            for inst in narrative.active_instances:
                if inst.instance_id in newly_activated:
                    inst.status = InstanceStatus.ACTIVE

        logger.info(f"Newly activated: {newly_activated}")
        return newly_activated

    def _check_dependencies(
        self,
        dependencies: List[str],
        active_instances: List["ModuleInstance"],
        history_ids: List[str]
    ) -> bool:
        """
        Check if all dependencies are completed (legacy method, kept for compatibility)

        Args:
            dependencies: List of dependency instance_ids
            active_instances: Currently active instances
            history_ids: List of completed instance_ids

        Returns:
            bool: Whether all dependencies are completed
        """
        if not dependencies:
            return True

        for dep_id in dependencies:
            # Found in history means completed
            if dep_id in history_ids:
                continue

            # Still in active means not completed
            if any(inst.instance_id == dep_id for inst in active_instances):
                return False

            # Neither in history nor in active
            logger.warning(f"Dependency {dep_id} not found")
            return False

        return True

    def _check_dependencies_from_db(
        self,
        dependencies: List[str],
        active_ids: List[str],
        history_ids: List[str]
    ) -> bool:
        """
        Check if all dependencies are completed (using database ID lists)

        Args:
            dependencies: List of dependency instance_ids
            active_ids: List of currently active instance_ids
            history_ids: List of completed instance_ids

        Returns:
            bool: Whether all dependencies are completed
        """
        if not dependencies:
            return True

        for dep_id in dependencies:
            # Found in history means completed
            if dep_id in history_ids:
                continue

            # Still in active means not completed
            if dep_id in active_ids:
                return False

            # Neither in history nor in active
            logger.warning(f"Dependency {dep_id} not found in links")
            return False

        return True
