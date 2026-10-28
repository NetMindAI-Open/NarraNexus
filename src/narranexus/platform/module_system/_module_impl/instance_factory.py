"""
@file_name: instance_factory.py
@author: NetMind.AI
@date: 2025-12-24
@description: Instance creation factory

Uses different creation strategies based on Module type:
- Agent level: Automatically created when creating an Agent (AwarenessModule, SocialNetworkModule, BasicInfoModule, MessageBusModule, LarkModule)
- Narrative level: Created when creating a Narrative (ChatModule)
- Task level: Created each time a task is created (JobModule)

Usage:
    factory = InstanceFactory(db_client)

    # When creating an Agent
    await factory.create_agent_level_instances(agent_id)

    # When creating a Narrative
    instance = await factory.create_chat_instance(agent_id, user_id, narrative_id)

    # When creating a Job
    instance = await factory.create_job_instance(agent_id, user_id, job_info)
"""

from typing import Optional, Dict, Any, List
import uuid
from loguru import logger

from narranexus.platform.utils import utc_now

from narranexus.platform.schema.instance_schema import (
    ModuleInstanceRecord,
    InstanceStatus,
)
from narranexus.platform.repository import InstanceRepository, InstanceNarrativeLinkRepository
# Peer-discovery policy lives with the table's own package. Module level is
# safe here (message_bus is a package, not a Module — iron rule #3 is about
# Modules importing each other) and both import orders were verified.
from narranexus.platform.message_bus.agent_discovery_sync import sync_agent_discovery


def _role_module(role: str) -> str:
    """Class name of the module declaring ``role``; fails loud when that plugin is disabled."""
    from narranexus.platform.module_system import module_by_role

    name = module_by_role(role)
    if name is None:
        raise RuntimeError(f"no registered module declares the {role!r} role")
    return name


def _prefix(module_class: str) -> str:
    from narranexus.platform.module_system import instance_prefix_for

    return instance_prefix_for(module_class)


def generate_instance_id(prefix: str) -> str:
    """
    Generate Instance ID

    Format: {prefix}_{uuid8}
    Example: chat_a1b2c3d4, job_e5f6g7h8
    """
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class InstanceFactory:
    """
    Instance Creation Factory

    Responsible for creating ModuleInstance based on different strategies and handling database interactions.

    Design notes:
    - Agent level Instance (is_public=True, user_id=None)
      - AwarenessModule: One per Agent, stores Agent's self-awareness
      - SocialNetworkModule: One per Agent, stores social relationship network
      - BasicInfoModule: One per Agent, provides basic information and environment context
      - MessageBusModule: One per Agent, inter-agent communication
      - LarkModule: One per Agent, Lark/Feishu bot binding

    - Narrative level Instance
      - ChatModule: One main_chat instance per Narrative

    - Task level Instance
      - JobModule: One instance per task
    """

    def __init__(self, db_client):
        """
        Initialize InstanceFactory

        Args:
            db_client: Database client
        """
        self._db = db_client
        self._instance_repo = InstanceRepository(db_client)
        self._link_repo = InstanceNarrativeLinkRepository(db_client)

    # ===== Agent Level Instance =====

    async def create_agent_level_instances(self, agent_id: str) -> List[ModuleInstanceRecord]:
        """
        Create Agent-level Instances

        Called when creating an Agent: one public instance per module whose
        ``ModuleConfig.agent_instance`` is declared (awareness, social network,
        basic info, message bus, Lark, Home Assistant — and any plugin module
        declaring it), in priority order.

        Args:
            agent_id: Agent ID

        Returns:
            List of created Instances
        """
        logger.info(f"Creating agent-level instances for agent: {agent_id}")

        instances = []

        # One instance per module declaring ``agent_instance`` (core modules
        # and channels alike; a plugin module declares its own).
        for module_class, cfg in self._agent_instance_modules():
            created = await self._create_declared_instance(agent_id, module_class, cfg)
            if created:
                instances.append(created)

        # Auto-register agent in MessageBus registry
        await self._register_agent_in_bus(agent_id)

        logger.info(f"Created {len(instances)} agent-level instances")
        return instances

    @staticmethod
    def _agent_instance_modules() -> List[tuple[str, Any]]:
        """``(module_class, ModuleConfig)`` for every registered module declaring
        ``agent_instance``, by priority — the platform holds no list of them."""
        from narranexus.platform.module_system import module_configs

        picked = [(cfg.priority, name, cfg) for name, cfg in module_configs().items() if cfg.agent_instance is not None]
        return [(name, cfg) for _, name, cfg in sorted(picked, key=lambda t: (t[0], t[1]))]

    async def _create_declared_instance(self, agent_id: str, module_class: str, cfg: Any) -> Optional[ModuleInstanceRecord]:
        """Create the module's public agent-level instance (idempotent per agent)."""
        existing = await self._instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class=module_class,
            is_public=True
        )
        if existing:
            logger.debug(f"{module_class} instance already exists for agent {agent_id}")
            return existing[0]

        spec = cfg.agent_instance
        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id(cfg.effective_instance_prefix()),
            module_class=module_class,
            agent_id=agent_id,
            user_id=None,
            is_public=True,
            status=InstanceStatus.ACTIVE,
            description=spec.description or cfg.description,
            keywords=list(spec.keywords) or [module_class.lower().replace("module", "")],
            topic_hint=spec.topic_hint,
            created_at=utc_now(),
        )

        await self._instance_repo.create_instance(instance)
        logger.info(f"Created {module_class} instance: {instance.instance_id}")
        return instance

    async def ensure_role_instance(self, agent_id: str, role: str) -> Optional[ModuleInstanceRecord]:
        """The public instance of the module declaring ``role`` ("awareness",
        "social_network", …), created from its declaration when missing. None
        when no registered module has the role (that plugin is disabled)."""
        from narranexus.platform.module_system import module_by_role, module_config

        module_class = module_by_role(role)
        if module_class is None:
            return None
        cfg = module_config(module_class)
        if cfg is None or cfg.agent_instance is None:
            existing = await self._instance_repo.get_by_agent(agent_id=agent_id, module_class=module_class, is_public=True)
            return existing[0] if existing else None
        return await self._create_declared_instance(agent_id, module_class, cfg)

    async def _register_agent_in_bus(self, agent_id: str) -> None:
        """Make the agent discoverable by its peers, from creation onward.

        Delegates to the one place that decides what a discovery row says. This
        used to be a hand-rolled upsert against ``bus_agent_registry`` with
        ``capabilities=json.dumps([])`` and its own copy of the description
        rule — the same defect that killed A2A discovery in production, in a
        third file (review 2026-08-05).

        It matters at all four provisioning paths, and two of them had no other
        sync behind them: bundle/migration import (``migration/applier.py``) and
        arena provisioning both left a row with empty capabilities until
        something unrelated happened to refresh it. Routing through the seam
        fixes them without either caller changing.
        """
        await sync_agent_discovery(self._db, agent_id)

    async def get_agent_level_instances(self, agent_id: str) -> List[ModuleInstanceRecord]:
        """
        Get Agent-level Instances

        Args:
            agent_id: Agent ID

        Returns:
            List of Agent-level Instances
        """
        return await self._instance_repo.get_public_instances(agent_id)

    # ===== Narrative Level Instance =====

    async def create_chat_instance(
        self,
        agent_id: str,
        user_id: str,
        narrative_id: str,
        description: Optional[str] = None
    ) -> ModuleInstanceRecord:
        """
        Create ChatModule Instance

        Called when creating a Narrative, as the main_chat instance.

        Args:
            agent_id: Agent ID
            user_id: User ID
            narrative_id: Narrative ID (for establishing association)
            description: Optional description

        Returns:
            Created ChatModule Instance
        """
        module_class = _role_module("chat")
        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id(_prefix(module_class)),
            module_class=module_class,
            agent_id=agent_id,
            user_id=user_id,
            is_public=False,
            status=InstanceStatus.ACTIVE,
            description=description or "Chat management and history",
            keywords=["chat", "conversation", "dialogue"],
            topic_hint="Chat interactions and message history",
            created_at=utc_now(),
        )

        await self._instance_repo.create_instance(instance)
        logger.info(f"Created ChatModule instance: {instance.instance_id}")

        # Establish association with Narrative
        await self._link_repo.link(instance.instance_id, narrative_id, "active")

        return instance

    # ===== Task Level Instance =====

    async def create_job_instance(
        self,
        agent_id: str,
        user_id: str,
        job_info: Dict[str, Any],
        narrative_id: Optional[str] = None
    ) -> ModuleInstanceRecord:
        """
        Create JobModule Instance

        Called each time a task is created.

        Args:
            agent_id: Agent ID
            user_id: User ID
            job_info: Job information (title, description, job_type, etc.)
            narrative_id: Optional, associated Narrative ID

        Returns:
            Created JobModule Instance
        """
        title = job_info.get("title", "Untitled Task")
        job_type = job_info.get("job_type", "one_off")

        module_class = _role_module("jobs")
        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id(_prefix(module_class)),
            module_class=module_class,
            agent_id=agent_id,
            user_id=user_id,
            is_public=False,
            status=InstanceStatus.ACTIVE,
            description=f"Execute task: {title}",
            keywords=["job", "task", job_type],
            topic_hint=title,
            config=job_info,
            state={
                "job_type": job_type,
                "progress": [],
            },
            created_at=utc_now(),
        )

        await self._instance_repo.create_instance(instance)
        logger.info(f"Created JobModule instance: {instance.instance_id}")

        # If there is an associated Narrative, establish the association
        if narrative_id:
            await self._link_repo.link(instance.instance_id, narrative_id, "active")

        return instance

    # ===== General Methods =====

    async def load_instances_for_narrative(
        self,
        agent_id: str,
        user_id: str,
        narrative_id: str
    ) -> List[ModuleInstanceRecord]:
        """
        Load all required Instances for a Narrative

        Flow:
        1. Load public instances (Agent level: awareness, social_network, basic_info, rag)
        2. Load narrative-associated instances (via links table)
        3. Merge and deduplicate

        Args:
            agent_id: Agent ID
            user_id: User ID
            narrative_id: Narrative ID

        Returns:
            List of all accessible Instances
        """
        logger.debug(f"Loading instances for narrative: {narrative_id}")

        # 1. Public instances (Agent level)
        public_instances = await self._instance_repo.get_public_instances(agent_id)

        # 2. Narrative-associated instances
        linked_ids = await self._link_repo.get_instances_for_narrative(narrative_id)
        linked_instances = []
        for inst_id in linked_ids:
            inst = await self._instance_repo.get_by_instance_id(inst_id)
            # Load instances with active and in_progress status
            # in_progress is mainly for JobModule (executing ONGOING jobs)
            valid_statuses = [
                InstanceStatus.ACTIVE.value, "active",
                InstanceStatus.IN_PROGRESS.value, "in_progress"
            ]
            if inst and inst.status in valid_statuses:
                # Chat-history modules are per-user: only load the current
                # user's instances. Other users' chat instances stay out of
                # the hook execution list (the agent can still query any
                # user's history via get_chat_history(instance_id=...)).
                from narranexus.platform.module_system import module_class_provides_chat_history
                if module_class_provides_chat_history(inst.module_class) and inst.user_id != user_id:
                    logger.debug(
                        f"Skipping other user's chat instance: {inst_id} "
                        f"(belongs to {inst.user_id}, current user is {user_id})"
                    )
                    continue
                linked_instances.append(inst)

        # 3. Merge and deduplicate
        seen_ids = set()
        result = []
        for inst in public_instances + linked_instances:
            if inst.instance_id in seen_ids:
                continue
            seen_ids.add(inst.instance_id)
            result.append(inst)

        logger.debug(f"Loaded {len(result)} instances for narrative")
        return result

    async def ensure_agent_instances_exist(self, agent_id: str) -> List[ModuleInstanceRecord]:
        """
        Ensure Agent-level Instances exist

        Creates all missing instances. When new module types are added
        (e.g. MessageBusModule), existing agents will get them on next load.

        Args:
            agent_id: Agent ID

        Returns:
            List of Agent-level Instances
        """
        existing = await self.get_agent_level_instances(agent_id)
        if not existing:
            return await self.create_agent_level_instances(agent_id)

        # Check for missing module types and create them
        existing_classes = {inst.module_class for inst in existing}
        for module_class, cfg in self._agent_instance_modules():
            if module_class not in existing_classes:
                new_inst = await self._create_declared_instance(agent_id, module_class, cfg)
                if new_inst:
                    existing.append(new_inst)
                    logger.info(f"Auto-created missing {module_class} instance for agent {agent_id}")

        return existing
