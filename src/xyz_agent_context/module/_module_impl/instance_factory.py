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

from xyz_agent_context.utils import utc_now

from xyz_agent_context.schema.instance_schema import (
    ModuleInstanceRecord,
    InstanceStatus,
)
from xyz_agent_context.repository import InstanceRepository, InstanceNarrativeLinkRepository


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

        Called when creating an Agent, will create the following Instances:
        - AwarenessModule instance (is_public=True)
        - SocialNetworkModule instance (is_public=True)
        - BasicInfoModule instance (is_public=True)
        - MessageBusModule instance (is_public=True)
        - LarkModule instance (is_public=True)

        Args:
            agent_id: Agent ID

        Returns:
            List of created Instances
        """
        logger.info(f"Creating agent-level instances for agent: {agent_id}")

        instances = []

        # 1. Create AwarenessModule instance
        awareness_instance = await self._create_awareness_instance(agent_id)
        if awareness_instance:
            instances.append(awareness_instance)

        # 2. Create SocialNetworkModule instance
        social_instance = await self._create_social_network_instance(agent_id)
        if social_instance:
            instances.append(social_instance)

        # 3. Create BasicInfoModule instance
        basic_info_instance = await self._create_basic_info_instance(agent_id)
        if basic_info_instance:
            instances.append(basic_info_instance)

        # 4. Create MessageBusModule instance
        bus_instance = await self._create_message_bus_instance(agent_id)
        if bus_instance:
            instances.append(bus_instance)

        # 5. Create LarkModule instance (agent may later bind a Feishu bot)
        lark_instance = await self._create_lark_instance(agent_id)
        if lark_instance:
            instances.append(lark_instance)

        # 6. Auto-register agent in MessageBus registry
        await self._register_agent_in_bus(agent_id)

        logger.info(f"Created {len(instances)} agent-level instances")
        return instances

    async def _create_awareness_instance(self, agent_id: str) -> Optional[ModuleInstanceRecord]:
        """Create AwarenessModule Instance"""
        # Check if already exists
        existing = await self._instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="AwarenessModule",
            is_public=True
        )
        if existing:
            logger.debug(f"AwarenessModule instance already exists for agent {agent_id}")
            return existing[0]

        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id("aware"),
            module_class="AwarenessModule",
            agent_id=agent_id,
            user_id=None,
            is_public=True,
            status=InstanceStatus.ACTIVE,
            description="Agent self-awareness and cognitive state management",
            keywords=["awareness", "self", "cognition"],
            topic_hint="Agent's self-cognition, goals and state",
            created_at=utc_now(),
        )

        await self._instance_repo.create_instance(instance)
        logger.info(f"Created AwarenessModule instance: {instance.instance_id}")
        return instance

    async def _create_social_network_instance(self, agent_id: str) -> Optional[ModuleInstanceRecord]:
        """Create SocialNetworkModule Instance"""
        # Check if already exists
        existing = await self._instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule",
            is_public=True
        )
        if existing:
            logger.debug(f"SocialNetworkModule instance already exists for agent {agent_id}")
            return existing[0]

        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id("social"),
            module_class="SocialNetworkModule",
            agent_id=agent_id,
            user_id=None,
            is_public=True,
            status=InstanceStatus.ACTIVE,
            description="Agent social network and entity relationship management",
            keywords=["social", "network", "relationship", "entity"],
            topic_hint="Social relationship network, user and entity information",
            created_at=utc_now(),
        )

        await self._instance_repo.create_instance(instance)
        logger.info(f"Created SocialNetworkModule instance: {instance.instance_id}")
        return instance

    async def _create_basic_info_instance(self, agent_id: str) -> Optional[ModuleInstanceRecord]:
        """Create BasicInfoModule Instance"""
        # Check if already exists
        existing = await self._instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="BasicInfoModule",
            is_public=True
        )
        if existing:
            logger.debug(f"BasicInfoModule instance already exists for agent {agent_id}")
            return existing[0]

        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id("basic"),
            module_class="BasicInfoModule",
            agent_id=agent_id,
            user_id=None,
            is_public=True,
            status=InstanceStatus.ACTIVE,
            description="Basic information and environment context",
            keywords=["basic", "info", "time", "context"],
            topic_hint="Basic information, time, environment context",
            created_at=utc_now(),
        )

        await self._instance_repo.create_instance(instance)
        logger.info(f"Created BasicInfoModule instance: {instance.instance_id}")
        return instance

    async def _create_message_bus_instance(self, agent_id: str) -> Optional[ModuleInstanceRecord]:
        """Create MessageBusModule Instance"""
        existing = await self._instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="MessageBusModule",
            is_public=True
        )
        if existing:
            logger.debug(f"MessageBusModule instance already exists for agent {agent_id}")
            return existing[0]

        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id("bus"),
            module_class="MessageBusModule",
            agent_id=agent_id,
            user_id=None,
            is_public=True,
            status=InstanceStatus.ACTIVE,
            description="Agent-to-agent communication via message bus",
            keywords=["message_bus", "communication", "messaging", "agent"],
            topic_hint="Inter-agent messaging via message bus",
            created_at=utc_now(),
        )

        await self._instance_repo.create_instance(instance)
        logger.info(f"Created MessageBusModule instance: {instance.instance_id}")
        return instance

    async def _create_lark_instance(self, agent_id: str) -> Optional[ModuleInstanceRecord]:
        """Create LarkModule Instance"""
        existing = await self._instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="LarkModule",
            is_public=True
        )
        if existing:
            logger.debug(f"LarkModule instance already exists for agent {agent_id}")
            return existing[0]

        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id("lark"),
            module_class="LarkModule",
            agent_id=agent_id,
            user_id=None,
            is_public=True,
            status=InstanceStatus.ACTIVE,
            description="Lark/Feishu integration: contacts, messages, documents, calendar, tasks",
            keywords=["lark", "feishu", "im", "messaging", "document", "calendar"],
            topic_hint="Lark/Feishu bot operations and IM interactions",
            created_at=utc_now(),
        )

        await self._instance_repo.create_instance(instance)
        logger.info(f"Created LarkModule instance: {instance.instance_id}")
        return instance

    async def _register_agent_in_bus(self, agent_id: str) -> None:
        """Register agent in MessageBus agent registry for discovery by other agents."""
        try:
            # Get agent info
            agent_row = await self._db.get_one("agents", {"agent_id": agent_id})
            if not agent_row:
                return

            owner = agent_row.get("created_by", "")
            name = agent_row.get("agent_name", "")
            desc = agent_row.get("agent_description", "")
            is_public = agent_row.get("is_public", 0)

            # Upsert into bus_agent_registry
            import json
            from datetime import datetime, timezone as dt_tz
            now = datetime.now(dt_tz.utc).isoformat()
            await self._db.upsert("bus_agent_registry", {
                "agent_id": agent_id,
                "owner_user_id": owner,
                "capabilities": json.dumps([]),
                "description": f"{name}: {desc}" if desc else name,
                "visibility": "public" if is_public else "private",
                "registered_at": now,
                "last_seen_at": now,
            }, "agent_id")
            logger.info(f"Registered agent {agent_id} ({name}) in MessageBus registry")
        except Exception as e:
            logger.warning(f"Failed to register agent {agent_id} in MessageBus: {e}")

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
        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id("chat"),
            module_class="ChatModule",
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

        instance = ModuleInstanceRecord(
            instance_id=generate_instance_id("job"),
            module_class="JobModule",
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

        # Historical module classes that may still have stale DB records —
        # skip them at load time. GeminiRAGModule source code still exists
        # (for RAG file management) but is not registered in MODULE_MAP
        # until per-user Gemini API key support is wired through ContextVar.
        DISABLED_MODULES = {"GeminiRAGModule"}

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
                # For ChatModule, only load current user's instances
                # Other users' ChatModule instances should not be loaded into the hook execution list
                # Agent can query any user's history via get_chat_history(instance_id=...)
                if inst.module_class == "ChatModule" and inst.user_id != user_id:
                    logger.debug(
                        f"Skipping other user's ChatModule instance: {inst_id} "
                        f"(belongs to {inst.user_id}, current user is {user_id})"
                    )
                    continue
                linked_instances.append(inst)

        # 3. Merge, deduplicate, and filter disabled modules
        seen_ids = set()
        result = []
        for inst in public_instances + linked_instances:
            if inst.instance_id in seen_ids:
                continue
            if inst.module_class in DISABLED_MODULES:
                logger.debug(
                    f"Skipping disabled module instance: {inst.instance_id} "
                    f"({inst.module_class})"
                )
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
        # GeminiRAGModule is intentionally excluded — the module is temporarily
        # disabled pending per-user Gemini API key support via ContextVar.
        # Existing RAG instances in the database are ignored at load time too.
        creators = {
            "AwarenessModule": self._create_awareness_instance,
            "SocialNetworkModule": self._create_social_network_instance,
            "BasicInfoModule": self._create_basic_info_instance,
            "MessageBusModule": self._create_message_bus_instance,
            "LarkModule": self._create_lark_instance,
        }
        for module_class, creator_fn in creators.items():
            if module_class not in existing_classes:
                new_inst = await creator_fn(agent_id)
                if new_inst:
                    existing.append(new_inst)
                    logger.info(f"Auto-created missing {module_class} instance for agent {agent_id}")

        return existing
