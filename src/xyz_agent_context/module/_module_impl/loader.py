"""
@file_name: loader.py
@author: NetMind.AI
@date: 2025-12-22
@description: Module loader

Responsible for creating Module instances by module name and binding them to ModuleInstance.

Refactoring notes (2025-12-24):
- Added logic to load Instances from database
- Load Instances via InstanceFactory
- Fallback mechanism: uses narrative.active_instances when database loading fails
"""

import json
from typing import List, Dict, Optional, TYPE_CHECKING
from datetime import datetime
from uuid import uuid4
from loguru import logger

from xyz_agent_context.schema import ExecutionPath, ModuleLoadResult
from xyz_agent_context.schema.module_schema import ModuleInstance, InstanceStatus
from xyz_agent_context.schema.instance_schema import ModuleInstanceRecord

from .instance_decision import (
    llm_decide_instances,
    dict_to_module_instance,
    _get_default_decision,
)
from .instance_factory import InstanceFactory

from xyz_agent_context.services.instance_sync_service import InstanceSyncService
from xyz_agent_context.settings import settings

if TYPE_CHECKING:
    from xyz_agent_context.module import XYZBaseModule
    from xyz_agent_context.narrative import Narrative
    from xyz_agent_context.utils import DatabaseClient


class ModuleLoader:
    """
    Module Loader

    Responsible for:
    1. Creating Module instances
    2. Binding Module to ModuleInstance
    3. Calculating Instance changes

    Refactoring notes:
    - Supports loading Instances from database (via InstanceFactory)
    - Fallback mechanism: uses narrative.active_instances when database loading fails
    """

    DEFAULT_MODULE_LIST = [
        "MemoryModule",
        "AwarenessModule",
        "ChatModule",
        "BasicInfoModule",
        "SocialNetworkModule",
        "JobModule",
        "MessageBusModule",
        "LarkModule",
    ]

    # Always-loaded modules (no Instance record needed, loaded directly)
    # These modules are independent of the database Instance mechanism and are always auto-loaded
    ALWAYS_LOAD_MODULES = [
        "SkillModule",
    ]

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        database_client: "DatabaseClient",
        module_map: Dict[str, type],
    ):
        """
        Initialize ModuleLoader

        Args:
            agent_id: Agent ID
            user_id: User ID
            database_client: Database client
            module_map: Mapping of module names to classes
        """
        self.agent_id = agent_id
        self.user_id = user_id
        self.database_client = database_client
        self.module_map = module_map
        self._instance_factory: Optional[InstanceFactory] = None
        self._instance_sync_service: Optional[InstanceSyncService] = None

    @property
    def instance_factory(self) -> InstanceFactory:
        """Lazy-load InstanceFactory"""
        if self._instance_factory is None:
            self._instance_factory = InstanceFactory(self.database_client)
        return self._instance_factory

    @property
    def instance_sync_service(self) -> InstanceSyncService:
        """Lazy-load InstanceSyncService"""
        if self._instance_sync_service is None:
            self._instance_sync_service = InstanceSyncService(self.database_client)
        return self._instance_sync_service

    async def load_modules(
        self,
        narrative_list: List["Narrative"],
        module_name_list: Optional[List[str]] = None,
        input_content: Optional[str] = None,
        use_instance_decision: bool = True,
        narrative_summary: str = "",
        markdown_history: str = "",
        awareness: str = "",
        working_source: Optional[str] = None,
    ) -> ModuleLoadResult:
        """
        Load Module instances and decide execution path

        Supports two modes:
        1. Instance decision mode (default): Uses LLM for intelligent Instance management
        2. Traditional mode: Uses module_name_list or default module list

        Args:
            narrative_list: Narrative list
            module_name_list: Specified module name list (traditional mode)
            input_content: User input content (required for Instance decision mode)
            use_instance_decision: Whether to use LLM Instance intelligent decision (default True)
            narrative_summary: Narrative summary
            markdown_history: History
            awareness: Agent awareness content
            working_source: Working source

        Returns:
            ModuleLoadResult
        """
        # ===== Instance decision mode (default) =====
        if use_instance_decision and input_content is not None:
            return await self._load_with_instance_decision(
                narrative_list=narrative_list,
                input_content=input_content,
                narrative_summary=narrative_summary,
                markdown_history=markdown_history,
                awareness=awareness,
                working_source=working_source,
            )

        # ===== Traditional mode =====
        if use_instance_decision and input_content is None:
            logger.warning("ModuleLoader: Instance decision mode requires input_content, falling back to traditional mode")

        return await self._load_traditional(module_name_list, working_source=working_source)

    async def _load_with_instance_decision(
        self,
        narrative_list: List["Narrative"],
        input_content: str,
        narrative_summary: str,
        markdown_history: str,
        awareness: str = "",
        working_source: Optional[str] = None,
    ) -> ModuleLoadResult:
        """
        Load modules using LLM Instance decision mode

        Refactored flow:
        1. Load currently available Instances from database (via InstanceFactory)
        2. Separate capability modules (rule-loaded) and task modules (LLM-decided)
        3. Keep capability modules directly, only pass task modules to LLM for decision
        4. Merge both, create Module objects and bind
        5. Return ModuleLoadResult

        Args:
            narrative_list: Narrative list
            input_content: User input content
            narrative_summary: Narrative summary
            markdown_history: History
            awareness: Agent self-awareness content
        """
        logger.info("ModuleLoader: Using LLM Instance intelligent decision mode")
        main_narrative = narrative_list[0] if narrative_list else None
        narrative_id = main_narrative.id if main_narrative else None

        # ===== Fast-path: skip LLM decision, load all capability modules directly =====
        if settings.skip_module_decision_llm:
            logger.info("ModuleLoader: SKIP_MODULE_DECISION_LLM is enabled, bypassing LLM instance decision")
            return await self._load_all_capability_modules(main_narrative, working_source=working_source)

        # ===== Load Instances from database =====
        current_instances = await self._load_current_instances(main_narrative)
        logger.debug(f"ModuleLoader: Loaded {len(current_instances)} Instances from database")

        # ===== Separate capability and task modules =====
        capability_instances = []
        task_instances = []

        for inst in current_instances:
            module_class = self.module_map.get(inst.module_class)
            if module_class:
                # Create temporary instance to get config
                temp_module = module_class(self.agent_id, self.user_id, self.database_client)
                module_type = temp_module.config.module_type
                if module_type == "task":
                    task_instances.append(inst)
                else:
                    capability_instances.append(inst)
            else:
                # Unknown module type, default to capability
                capability_instances.append(inst)

        logger.info(
            f"ModuleLoader: Separated modules - capability: {len(capability_instances)}, "
            f"task: {len(task_instances)}"
        )

        # ===== Build capability module info (passed to LLM) =====
        capability_info = []
        for inst in capability_instances:
            module_class = self.module_map.get(inst.module_class)
            if module_class:
                temp_module = module_class(self.agent_id, self.user_id, self.database_client)
                capability_info.append({
                    "module_class": inst.module_class,
                    "instance_id": inst.instance_id,
                    "description": temp_module.config.description
                })

        # ===== Build job_info_map (for displaying Job's related_entity_id) =====
        # [Important Fix] Query all active Jobs for the current Narrative directly from instance_jobs table
        # instead of relying only on instance_narrative_links, ensuring LLM can see all existing Jobs
        job_info_map = {}
        from xyz_agent_context.repository import JobRepository
        job_repo = JobRepository(self.database_client)

        if narrative_id:
            # Query all active Jobs under this Narrative directly (not relying on link)
            all_narrative_jobs = await job_repo.get_active_jobs_by_narrative(
                narrative_id=narrative_id,
                limit=100
            )
            logger.info(
                f"ModuleLoader: Loaded {len(all_narrative_jobs)} active Jobs directly from instance_jobs table"
            )

            for job in all_narrative_jobs:
                job_info_map[job.instance_id] = {
                    "related_entity_id": job.related_entity_id,
                    "job_type": job.job_type.value if hasattr(job.job_type, 'value') else str(job.job_type),
                    "title": job.title,
                }

                # [Supplement] If this Job's ModuleInstance is not in task_instances,
                # create a temporary ModuleInstance so LLM can see it
                if not any(inst.instance_id == job.instance_id for inst in task_instances):
                    from xyz_agent_context.schema.module_schema import ModuleInstance, InstanceStatus
                    temp_instance = ModuleInstance(
                        instance_id=job.instance_id,
                        module_class="JobModule",
                        description=job.description or f"Job: {job.title}",
                        status=InstanceStatus.ACTIVE,
                        agent_id=self.agent_id,
                        dependencies=[],
                        created_at=job.created_at,
                        last_used_at=job.updated_at or job.created_at,
                    )
                    task_instances.append(temp_instance)
                    logger.warning(
                        f"ModuleLoader: Supplementing missing Job Instance: {job.instance_id} ({job.title})"
                    )

        # ===== Call LLM for Instance decision (only decide task modules) =====
        llm_error: str | None = None
        try:
            decision_output = await llm_decide_instances(
                user_input=input_content,
                agent_id=self.agent_id,
                current_instances=task_instances,  # Only pass task modules
                narrative_summary=narrative_summary,
                markdown_history=markdown_history,
                awareness=awareness,
                capability_modules=capability_info,  # Inform LLM of loaded capabilities
                current_user_id=self.user_id,  # Inform LLM of current user
                job_info_map=job_info_map  # Inform LLM of each Job's target user
            )
        except Exception as e:
            llm_error = f"Module decision LLM failed ({type(e).__name__}): {e}"
            logger.warning(f"ModuleLoader: {llm_error}, using fallback")
            decision_output = _get_default_decision(task_instances)

        # ===== Use InstanceSyncService to handle task_key conversion (only process task modules) =====
        processed_task_instances, key_to_id = await self.instance_sync_service.process_instance_decision(
            instances=decision_output.active_instances,
            agent_id=self.agent_id,
            user_id=self.user_id,
            narrative_id=narrative_id
        )
        logger.debug(f"ModuleLoader: task_key mapping: {key_to_id}")

        # Convert LLM-returned task InstanceDict to ModuleInstance objects
        task_active_instances = [
            dict_to_module_instance(inst_dict, self.agent_id)
            for inst_dict in processed_task_instances
        ]

        # ===== Merge capability modules and task modules =====
        # Capability modules are already ModuleInstance objects, use directly
        all_active_instances = capability_instances + task_active_instances

        logger.info(
            f"ModuleLoader: After merge - capability: {len(capability_instances)}, "
            f"task: {len(task_active_instances)}, total: {len(all_active_instances)}"
        )

        # ===== Fallback: ensure JobModule MCP tools are always accessible =====
        all_active_instances = self._ensure_job_module_available(all_active_instances)

        # ===== Add always-loaded modules (no Instance record needed) =====
        all_active_instances = self._add_always_load_modules(all_active_instances)

        # Create Module objects and bind to instances
        active_instances = self._create_module_objects(all_active_instances)

        # Calculate changes (only calculate task module changes)
        changes_summary = self._calculate_changes(task_instances, task_active_instances)

        # Parse execution_type
        execution_type = (
            ExecutionPath.AGENT_LOOP
            if decision_output.execution_path == "agent_loop"
            else ExecutionPath.DIRECT_TRIGGER
        )

        # Parse changes_explanation
        try:
            changes_explanation_dict = json.loads(decision_output.changes_explanation)
        except json.JSONDecodeError:
            logger.warning("Unable to parse changes_explanation JSON, using empty dict")
            changes_explanation_dict = {}

        logger.success(
            f"ModuleLoader: Instance decision complete, path={execution_type.value}, "
            f"Instances: {active_instances}"
        )

        return ModuleLoadResult(
            active_instances=active_instances,
            changes_summary=changes_summary,
            changes_explanation=changes_explanation_dict,
            decision_reasoning=decision_output.reasoning,
            execution_type=execution_type,
            direct_trigger=decision_output.direct_trigger,
            relationship_graph=decision_output.relationship_graph,
            llm_error=llm_error,
            # Complex Job support
            key_to_id=key_to_id,
            raw_instances=processed_task_instances
        )

    async def _load_all_capability_modules(
        self,
        narrative: Optional["Narrative"],
        working_source: Optional[str] = None,
    ) -> ModuleLoadResult:
        """
        Fast-path: Load all capability modules + JobModule + SkillModule
        without making an LLM call.

        Used when SKIP_MODULE_DECISION_LLM=True.  In production, the LLM
        instance decision always returns the same set of capability modules,
        so this shortcut saves ~2.5-3s per turn with identical results.
        """
        # Load current instances from DB (needed to preserve task module state)
        current_instances = await self._load_current_instances(narrative)

        # Keep all current instances (capability + task) as-is
        all_instances = list(current_instances)

        # Ensure JobModule and always-load modules are present
        all_instances = self._ensure_job_module_available(all_instances)
        all_instances = self._add_always_load_modules(all_instances)

        # Create Module objects and bind
        active_instances = self._create_module_objects(all_instances)

        logger.success(
            f"ModuleLoader: Fast-path complete (LLM skipped), "
            f"loaded {len(active_instances)} instances"
        )

        return ModuleLoadResult(
            active_instances=active_instances,
            changes_summary={"added": [], "removed": [], "updated": [], "kept": [
                inst.instance_id for inst in active_instances
            ]},
            changes_explanation={},
            decision_reasoning="LLM decision skipped (SKIP_MODULE_DECISION_LLM=True)",
            execution_type=ExecutionPath.AGENT_LOOP,
            direct_trigger=None,
            relationship_graph="",
            key_to_id={},
            raw_instances=[],
        )

    async def _load_current_instances(
        self,
        narrative: Optional["Narrative"]
    ) -> List[ModuleInstance]:
        """
        Load currently available Instances

        Prioritizes loading from database; falls back to narrative.active_instances on database failure (fallback mechanism)

        Args:
            narrative: Current Narrative

        Returns:
            List of ModuleInstance
        """
        if narrative is None:
            return []

        try:
            # Ensure agent-level instances exist (auto-creates missing ones like MessageBusModule)
            await self.instance_factory.ensure_agent_instances_exist(self.agent_id)

            # Attempt to load from database
            db_instances = await self.instance_factory.load_instances_for_narrative(
                agent_id=self.agent_id,
                user_id=self.user_id,
                narrative_id=narrative.id
            )

            if db_instances:
                # Convert ModuleInstanceRecord to ModuleInstance (backward compatible with old format)
                instances = self._convert_to_module_instances(db_instances)
                logger.debug(f"ModuleLoader: Loaded {len(instances)} Instances from database")
                return instances

        except Exception as e:
            logger.warning(f"ModuleLoader: Failed to load Instances from database: {e}, using fallback")

        # Fallback: use narrative.active_instances (backward compatible with old data)
        if hasattr(narrative, 'active_instances') and narrative.active_instances:
            logger.debug(
                f"ModuleLoader: Using narrative.active_instances fallback, "
                f"total {len(narrative.active_instances)} Instances"
            )
            return narrative.active_instances

        return []

    def _convert_to_module_instances(
        self,
        records: List[ModuleInstanceRecord]
    ) -> List[ModuleInstance]:
        """
        Convert ModuleInstanceRecord to ModuleInstance

        This is for backward compatibility with existing LLM decision logic, which expects ModuleInstance type.

        Args:
            records: List of ModuleInstanceRecord

        Returns:
            List of ModuleInstance
        """
        instances = []
        for record in records:
            # Convert database record to old format ModuleInstance
            status_value = record.status
            if isinstance(status_value, InstanceStatus):
                status_value = status_value.value

            # Map status
            status_map = {
                "active": InstanceStatus.ACTIVE,
                "in_progress": InstanceStatus.IN_PROGRESS,
                "blocked": InstanceStatus.BLOCKED,
                "completed": InstanceStatus.COMPLETED,
                "failed": InstanceStatus.FAILED,
                "archived": InstanceStatus.ACTIVE,  # archived maps to active (should not occur)
            }
            status = status_map.get(status_value, InstanceStatus.ACTIVE)

            instance = ModuleInstance(
                instance_id=record.instance_id,
                module_class=record.module_class,
                description=record.description or "",
                status=status,
                agent_id=record.agent_id,
                dependencies=record.dependencies or [],
                config=record.config or {},
                state=record.state,
                created_at=record.created_at,
                last_used_at=record.last_used_at,
            )
            instances.append(instance)

        return instances

    async def _load_traditional(
        self,
        module_name_list: Optional[List[str]] = None,
        working_source: Optional[str] = None,
    ) -> ModuleLoadResult:
        """
        Traditional mode module loading (without LLM)

        Used for testing scenarios or simple scenarios with a fixed module list.

        Args:
            module_name_list: Specified module name list, uses default list when None
            working_source: Working source
        """
        selected_modules = module_name_list or self.DEFAULT_MODULE_LIST

        # Add always-loaded modules (if not already in the list)
        for always_module in self.ALWAYS_LOAD_MODULES:
            if always_module not in selected_modules:
                selected_modules = list(selected_modules) + [always_module]

        logger.info(f"ModuleLoader: Traditional mode, loading modules: {selected_modules}")

        module_list = []
        for module_name in selected_modules:
            if module_name not in self.module_map:
                logger.warning(f"ModuleLoader: Unknown module name '{module_name}', skipping")
                continue
            module_class = self.module_map[module_name]
            module = module_class(self.agent_id, self.user_id, self.database_client)
            module_list.append(module)

        logger.success(f"ModuleLoader: Successfully loaded {len(module_list)} modules")

        return ModuleLoadResult(
            module_list=module_list,
            module_objects=module_list,
            execution_type=ExecutionPath.AGENT_LOOP,
        )

    def _create_module_objects(
        self,
        instances: List[ModuleInstance]
    ) -> List[ModuleInstance]:
        """
        Create actual Module objects from ModuleInstance list and bind them

        Args:
            instances: List of ModuleInstance

        Returns:
            List of ModuleInstance with bound modules
        """
        module_count = 0
        for inst in instances:
            if inst.module_class not in self.module_map:
                logger.warning(f"ModuleLoader: Unknown module type '{inst.module_class}', skipping")
                continue

            module_class = self.module_map[inst.module_class]
            module = module_class(
                self.agent_id,
                self.user_id,
                self.database_client,
                instance_id=inst.instance_id,
                instance_ids=[i.instance_id for i in instances]
            )

            inst.module = module
            module_count += 1
            logger.debug(
                f"ModuleLoader: Created and bound Module '{inst.module_class}' "
                f"to instance '{inst.instance_id}'"
            )

        logger.info(f"ModuleLoader: Created {module_count} Module objects and bound to instances")
        return instances

    def _calculate_changes(
        self,
        old_instances: List[ModuleInstance],
        new_instances: List[ModuleInstance]
    ) -> Dict[str, List[str]]:
        """
        Calculate Instance changes

        Args:
            old_instances: Old instances list
            new_instances: New instances list

        Returns:
            Change summary {added: [...], removed: [...], updated: [...], kept: [...]}
        """
        old_ids = {inst.instance_id for inst in old_instances}
        new_ids = {inst.instance_id for inst in new_instances}

        added = list(new_ids - old_ids)
        removed = list(old_ids - new_ids)
        kept = list(old_ids & new_ids)

        # Check if any kept instances have updates (status changes, etc.)
        updated = []
        for inst_id in kept:
            old_inst = next((i for i in old_instances if i.instance_id == inst_id), None)
            new_inst = next((i for i in new_instances if i.instance_id == inst_id), None)
            if old_inst and new_inst and old_inst.status != new_inst.status:
                updated.append(inst_id)

        return {
            "added": added,
            "removed": removed,
            "updated": updated,
            "kept": [k for k in kept if k not in updated]
        }

    def generate_instance_id(self, module_class: str) -> str:
        """
        Generate Instance ID

        Unified format: {module_prefix}_{uuid8}

        Args:
            module_class: Module class name

        Returns:
            Instance ID
        """
        short_uuid = uuid4().hex[:8]
        # Module prefix mapping
        prefix_map = {
            "MemoryModule": "memory",
            "ChatModule": "chat",
            "JobModule": "job",
            "SocialNetworkModule": "social",
            "AwarenessModule": "aware",
            "BasicInfoModule": "info",
            "SkillModule": "skill",
            "MessageBusModule": "bus",
            "LarkModule": "lark",
        }
        prefix = prefix_map.get(module_class, module_class.lower().replace("module", ""))
        return f"{prefix}_{short_uuid}"

    def _ensure_job_module_available(
        self,
        instances: List[ModuleInstance]
    ) -> List[ModuleInstance]:
        """
        Ensure JobModule MCP tools are always accessible.

        When instance decision selects zero JobModule instances, create a
        virtual (in-memory only, not persisted) JobModule instance so the
        Agent can still access job_create and other MCP tools in Step 3.

        Args:
            instances: Current instance list (capability + task merged)

        Returns:
            Instance list, with a virtual JobModule appended if none existed
        """
        has_job_module = any(
            inst.module_class == "JobModule"
            for inst in instances
        )

        if has_job_module or "JobModule" not in self.module_map:
            return instances

        virtual_instance = ModuleInstance(
            instance_id="",
            module_class="JobModule",
            description="",
            status=InstanceStatus.ACTIVE,
            agent_id=self.agent_id,
            dependencies=[],
            config={},
            state={},
            created_at=datetime.now(),
            last_used_at=datetime.now(),
        )

        logger.info(
            "ModuleLoader: No JobModule instance selected by decision, "
            "adding virtual instance to ensure MCP tools are available"
        )
        return list(instances) + [virtual_instance]

    def _add_always_load_modules(
        self,
        instances: List[ModuleInstance]
    ) -> List[ModuleInstance]:
        """
        Add always-loaded modules (no Instance record needed)

        These modules are independent of the database Instance mechanism and are always auto-loaded.
        For example, SkillModule is purely file-system based and does not need database records.

        Args:
            instances: Current instance list

        Returns:
            Instance list with always-loaded modules added
        """
        result = list(instances)

        for module_name in self.ALWAYS_LOAD_MODULES:
            # Check if already exists
            already_loaded = any(
                inst.module_class == module_name
                for inst in result
            )

            if not already_loaded and module_name in self.module_map:
                # Create temporary Instance (in-memory only, not written to database)
                instance_id = f"{module_name.lower().replace('module', '')}_default"
                temp_instance = ModuleInstance(
                    instance_id=instance_id,
                    module_class=module_name,
                    description="Always-loaded module (no database record)",
                    status=InstanceStatus.ACTIVE,
                    agent_id=self.agent_id,
                    dependencies=[],
                    config={},
                    state={},
                    created_at=datetime.now(),
                    last_used_at=datetime.now(),
                )
                result.append(temp_instance)
                logger.debug(f"ModuleLoader: Added always-loaded module {module_name}")

        return result
