"""
@file_name: instance_sync_service.py
@author: NetMind.AI
@date: 2025-12-25
@description: Instance sync service

Responsible for converting LLM Instance Decision outputs:
1. task_key -> instance_id mapping
2. depends_on -> dependencies conversion
3. Job record creation for JobModule

Workflow:
1. Receive the InstanceDict list output by the LLM
2. Generate real instance_id for each Instance
3. Convert semantic dependencies to real ID dependencies
4. Detect circular dependencies
5. Create Job records for JobModule
"""

from __future__ import annotations

from typing import List, Dict, Optional, Tuple, TYPE_CHECKING
from datetime import datetime
from uuid import uuid4

from loguru import logger
from xyz_agent_context.utils import utc_now

if TYPE_CHECKING:
    from xyz_agent_context.utils import DatabaseClient
    from xyz_agent_context.module import InstanceDict, JobConfig


# Module prefix mapping
MODULE_PREFIX_MAP = {
    "ChatModule": "chat",
    "JobModule": "job",
    "SocialNetworkModule": "social",
    "GeminiRAGModule": "rag",
    "AwarenessModule": "aware",
    "BasicInfoModule": "info",
}


class InstanceSyncService:
    """
    Instance sync service

    Processes LLM Instance Decision outputs, performing:
    1. task_key -> instance_id mapping
    2. depends_on -> dependencies conversion
    3. Job record creation for JobModule
    """

    def __init__(self, database_client: "DatabaseClient"):
        """
        Initialize the service

        Args:
            database_client: Database client
        """
        self.db = database_client

    async def process_instance_decision(
        self,
        instances: List["InstanceDict"],
        agent_id: str,
        user_id: str,
        narrative_id: Optional[str] = None
    ) -> Tuple[List["InstanceDict"], Dict[str, str]]:
        """
        Process Instance Decision outputs

        Performs the following conversions:
        1. Generate instance_id for each Instance (if not provided)
        2. Convert depends_on (task_key list) to dependencies (instance_id list)
        3. Detect circular dependencies

        Args:
            instances: InstanceDict list output by the LLM
            agent_id: Agent ID
            user_id: User ID
            narrative_id: Narrative ID (optional)

        Returns:
            (processed instances, task_key -> instance_id mapping)

        Raises:
            ValueError: If circular dependencies are detected
        """
        logger.info(f"InstanceSyncService: Processing {len(instances)} Instance(s)")

        # Step 1: Build task_key -> instance_id mapping
        key_to_id = self._build_key_to_id_mapping(instances)

        # Step 2: Convert depends_on -> dependencies
        for inst in instances:
            # Set instance_id
            if not inst.instance_id or inst.instance_id == inst.task_key:
                inst.instance_id = key_to_id.get(inst.task_key, inst.instance_id)

            # Convert dependencies
            if inst.depends_on:
                inst.dependencies = self._resolve_dependencies(inst.depends_on, key_to_id)
                logger.debug(
                    f"  {inst.task_key}: depends_on={inst.depends_on} → "
                    f"dependencies={inst.dependencies}"
                )

        # Step 3: Detect circular dependencies
        self._detect_circular_dependencies(instances, key_to_id)

        # Step 4: Set initial status (set to blocked if there are dependencies)
        self._set_initial_status(instances)

        logger.success(f"InstanceSyncService: Processing complete, mapping={key_to_id}")
        return instances, key_to_id

    async def create_jobs_for_instances(
        self,
        instances: List["InstanceDict"],
        agent_id: str,
        user_id: str,
        key_to_id: Dict[str, str],
        narrative_id: Optional[str] = None
    ) -> List[str]:
        """
        Create Job records for JobModule Instances

        Args:
            instances: Processed InstanceDict list
            agent_id: Agent ID
            user_id: User ID
            key_to_id: task_key -> instance_id mapping
            narrative_id: Narrative ID (used to associate conversation context)

        Returns:
            List of created job_ids
        """
        from xyz_agent_context.repository import JobRepository
        from xyz_agent_context.schema.job_schema import JobType, TriggerConfig
        from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding, prepare_job_text_for_embedding

        job_repo = JobRepository(self.db)
        created_job_ids = []

        # [Deduplication optimization] Get all active Jobs under the current Narrative for semantic similarity checking
        existing_jobs = []
        if narrative_id:
            existing_jobs = await job_repo.get_active_jobs_by_narrative(
                narrative_id=narrative_id,
                limit=100
            )
            logger.info(f"  Existing active Jobs: {len(existing_jobs)}")

        # [Batch deduplication] Track Job titles created in this batch to avoid intra-batch duplicates
        created_titles_this_batch = set()

        for inst in instances:
            if inst.module_class != "JobModule":
                continue

            if not inst.job_config:
                logger.warning(f"  JobModule Instance {inst.task_key} missing job_config, skipping")
                continue

            job_config = inst.job_config
            instance_id = key_to_id.get(inst.task_key, inst.instance_id)

            # [Batch deduplication] Check if a Job with the same title already exists in this batch
            if job_config.title in created_titles_this_batch:
                logger.warning(
                    f"  Skipping duplicate Job: '{job_config.title}' already created in this batch"
                )
                continue

            # [Semantic similarity deduplication] Check if a semantically similar Job already exists
            similar_job = self._find_similar_job(job_config.title, existing_jobs)
            if similar_job:
                logger.warning(
                    f"  Skipping duplicate Job: '{job_config.title}' is semantically similar to "
                    f"existing '{similar_job.title}' (ID: {similar_job.job_id})"
                )
                continue

            # Generate job_id
            job_id = f"job_{uuid4().hex[:12]}"

            # Determine job_type and build trigger_config
            job_type = JobType.ONE_OFF
            next_run_time = None
            trigger_config_dict = {}

            # Check if there is a cron expression (periodic task)
            cron_expr = getattr(job_config, 'cron', None)
            # Check fields for ONGOING type
            interval_seconds = getattr(job_config, 'interval_seconds', None)
            end_condition = getattr(job_config, 'end_condition', None)
            max_iterations = getattr(job_config, 'max_iterations', None)

            if interval_seconds and end_condition:
                # ONGOING type: has interval_seconds + end_condition
                job_type = JobType.ONGOING
                trigger_config_dict["interval_seconds"] = interval_seconds
                trigger_config_dict["end_condition"] = end_condition
                if max_iterations:
                    trigger_config_dict["max_iterations"] = max_iterations
                # ONGOING task starts the first check immediately
                next_run_time = utc_now()
                logger.info(f"  {inst.task_key}: ONGOING type task, interval={interval_seconds}s, end_condition={end_condition}")
            elif cron_expr:
                job_type = JobType.SCHEDULED
                trigger_config_dict["cron"] = cron_expr
                # next_run_time for periodic tasks is calculated by calculate_next_run_time
                from xyz_agent_context.repository.job_repository import calculate_next_run_time
                trigger_config = TriggerConfig(**trigger_config_dict)
                next_run_time = calculate_next_run_time(job_type, trigger_config)
            elif interval_seconds and not end_condition:
                # SCHEDULED type: has interval_seconds but no end_condition
                job_type = JobType.SCHEDULED
                trigger_config_dict["interval_seconds"] = interval_seconds
                from xyz_agent_context.repository.job_repository import calculate_next_run_time
                trigger_config = TriggerConfig(**trigger_config_dict)
                next_run_time = calculate_next_run_time(job_type, trigger_config)
            elif job_config.scheduled_at:
                # One-off scheduled task
                job_type = JobType.ONE_OFF
                try:
                    next_run_time = datetime.fromisoformat(job_config.scheduled_at)
                    trigger_config_dict["run_at"] = job_config.scheduled_at
                except ValueError:
                    logger.warning(f"  Invalid scheduled_at: {job_config.scheduled_at}")
            elif not inst.depends_on:
                # Immediate task with no dependencies -> execute immediately
                next_run_time = utc_now()
                logger.debug(f"  {inst.task_key}: Immediate task with no dependencies, setting next_run_time = now")
            # else: Task with dependencies -> next_run_time stays None, will be set after dependencies complete

            trigger_config = TriggerConfig(**trigger_config_dict)

            # Generate embedding
            embedding_text = prepare_job_text_for_embedding(
                job_config.title,
                inst.description,
                job_config.payload
            )
            embedding = await get_embedding(embedding_text)
            # Dual-write to embeddings_store
            from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import store_embedding
            await store_embedding("job", job_id, embedding, source_text=embedding_text)

            # Extract related_entity_id (Feature 2.2, changed to single value)
            related_entity_id = getattr(job_config, 'related_entity_id', None)

            # Create Job record (check if it already exists first)
            try:
                # Check if a Job already exists for this instance_id (instance_id has a unique constraint)
                existing_jobs = await job_repo.get_jobs_by_instance(instance_id, limit=1)
                if existing_jobs:
                    existing_job = existing_jobs[0]
                    logger.warning(
                        f"  Instance {instance_id} already has Job: {existing_job.job_id}, skipping creation. "
                        f"Use update_job if an update is needed."
                    )
                    created_job_ids.append(existing_job.job_id)
                    continue

                await job_repo.create_job(
                    agent_id=agent_id,
                    user_id=user_id,
                    job_id=job_id,
                    title=job_config.title,
                    description=inst.description,
                    job_type=job_type,
                    trigger_config=trigger_config,
                    payload=job_config.payload,
                    instance_id=instance_id,
                    notification_method="inbox",
                    next_run_time=next_run_time,
                    embedding=embedding,
                    related_entity_id=related_entity_id,  # Feature 2.2 (single value)
                    narrative_id=narrative_id  # Feature 3.1
                )
                created_job_ids.append(job_id)
                created_titles_this_batch.add(job_config.title)  # Track created titles
                logger.info(
                    f"  Created Job: {job_id} (instance={instance_id}, title={job_config.title}, "
                    f"related_entity={related_entity_id}, narrative={narrative_id})"
                )

                # Feature 2.2: Sync Job to Entity's related_job_ids
                # This allows the target user to find associated Narratives through the Entity when querying
                if related_entity_id:
                    await self._sync_job_to_entity(
                        job_id=job_id,
                        entity_id=related_entity_id,
                        agent_id=agent_id
                    )

                    # Feature P0-4: Add related_entity_id as PARTICIPANT to Narrative
                    # This allows the target user to match this Narrative via PARTICIPANT query when sending messages
                    if narrative_id:
                        await self._add_participant_to_narrative(
                            narrative_id=narrative_id,
                            participant_id=related_entity_id
                        )

            except Exception as e:
                logger.error(f"  Failed to create Job: {e}")

        return created_job_ids

    def _build_key_to_id_mapping(self, instances: List["InstanceDict"]) -> Dict[str, str]:
        """
        Build task_key -> instance_id mapping

        Rules:
        1. If the Instance already has a valid instance_id (format: prefix_xxx), use it
        2. Otherwise generate a new instance_id based on module_class

        Args:
            instances: InstanceDict list

        Returns:
            task_key -> instance_id mapping
        """
        import re
        # Valid instance_id format: prefix_xxxxxxxx (e.g. job_fe7382f7)
        valid_id_pattern = re.compile(r'^[a-z]+_[a-f0-9]{8}$')

        key_to_id: Dict[str, str] = {}

        for inst in instances:
            task_key = inst.task_key or inst.instance_id

            # If there is already a valid format instance_id, keep it
            if inst.instance_id and valid_id_pattern.match(inst.instance_id):
                key_to_id[task_key] = inst.instance_id
                logger.debug(f"  {task_key}: Keeping existing instance_id={inst.instance_id}")
            else:
                # Generate new instance_id
                prefix = MODULE_PREFIX_MAP.get(inst.module_class, "inst")
                new_id = f"{prefix}_{uuid4().hex[:8]}"
                key_to_id[task_key] = new_id
                logger.debug(f"  {task_key}: Generated new instance_id={new_id}")

        return key_to_id

    def _resolve_dependencies(
        self,
        depends_on: List[str],
        key_to_id: Dict[str, str]
    ) -> List[str]:
        """
        Convert depends_on (task_key list) to dependencies (instance_id list)

        Args:
            depends_on: task_key list
            key_to_id: task_key -> instance_id mapping

        Returns:
            instance_id list
        """
        dependencies = []
        for task_key in depends_on:
            if task_key in key_to_id:
                dependencies.append(key_to_id[task_key])
            else:
                logger.warning(f"    Dependent task_key not found: {task_key}")
        return dependencies

    def _detect_circular_dependencies(
        self,
        instances: List["InstanceDict"],
        key_to_id: Dict[str, str]
    ) -> None:
        """
        Detect circular dependencies

        Uses DFS to detect cycles in a directed graph.

        Args:
            instances: InstanceDict list
            key_to_id: task_key -> instance_id mapping

        Raises:
            ValueError: If circular dependencies are detected
        """
        # Build adjacency list
        graph: Dict[str, List[str]] = {}
        for inst in instances:
            task_key = inst.task_key or inst.instance_id
            graph[task_key] = inst.depends_on or []

        # DFS cycle detection
        visited = set()
        rec_stack = set()

        def dfs(node: str, path: List[str]) -> Optional[List[str]]:
            """DFS cycle detection, returns cycle path or None"""
            if node in rec_stack:
                # Cycle found, return cycle path
                cycle_start = path.index(node)
                return path[cycle_start:] + [node]

            if node in visited:
                return None

            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                result = dfs(neighbor, path)
                if result:
                    return result

            path.pop()
            rec_stack.remove(node)
            return None

        # Check all nodes
        for task_key in graph:
            if task_key not in visited:
                cycle = dfs(task_key, [])
                if cycle:
                    cycle_str = " → ".join(cycle)
                    raise ValueError(f"Circular dependency detected: {cycle_str}")

        logger.debug("  No circular dependencies")

    def _find_similar_job(self, new_title: str, existing_jobs: list) -> Optional[object]:
        """
        Check if a semantically similar Job already exists

        Uses multiple algorithms to detect duplicates:
        1. Substring containment: If one normalized title is a substring of the other
        2. n-gram similarity: If bigram similarity exceeds the threshold

        Args:
            new_title: Title of the new Job
            existing_jobs: List of existing Jobs

        Returns:
            The similar Job if found, otherwise None
        """
        import re

        def normalize_text(text: str) -> str:
            """Normalize text: remove digits, spaces, punctuation, and content in parentheses"""
            # Remove parentheses and their contents
            text = re.sub(r'[（(][^）)]*[）)]', '', text)
            # Remove digits and punctuation, keep only Chinese and English characters
            text = re.sub(r'[0-9\s\-_.,;:!?，。；：！？、（）()]', '', text.lower())
            return text

        def get_ngrams(text: str, n: int = 2) -> set:
            """Extract n-grams (character-level)"""
            text = normalize_text(text)
            if len(text) < n:
                return {text} if text else set()
            return {text[i:i+n] for i in range(len(text) - n + 1)}

        def is_similar(text1: str, text2: str) -> bool:
            """Determine whether two titles are similar"""
            norm1 = normalize_text(text1)
            norm2 = normalize_text(text2)

            # 1. Substring containment detection (whether the shorter is contained in the longer)
            if len(norm1) >= 4 and len(norm2) >= 4:
                shorter, longer = (norm1, norm2) if len(norm1) <= len(norm2) else (norm2, norm1)
                if shorter in longer:
                    return True

            # 2. n-gram similarity
            ngrams1 = get_ngrams(text1, n=2)
            ngrams2 = get_ngrams(text2, n=2)
            if not ngrams1 or not ngrams2:
                return False
            intersection = ngrams1 & ngrams2
            union = ngrams1 | ngrams2
            similarity = len(intersection) / len(union) if union else 0.0

            return similarity >= 0.5

        for job in existing_jobs:
            # Skip non-active Jobs
            job_status = getattr(job, 'status', None)
            if job_status and hasattr(job_status, 'value') and job_status.value in ('completed', 'failed', 'cancelled'):
                continue

            existing_title = getattr(job, 'title', '')
            if is_similar(new_title, existing_title):
                logger.debug(f"    Similarity check: '{new_title}' vs '{existing_title}' -> determined as duplicate")
                return job

        return None

    def _set_initial_status(self, instances: List["InstanceDict"]) -> None:
        """
        Set the initial status of Instances

        Rules:
        - **Only JobModule can be set to blocked**
        - Non-JobModule (capability-type Modules) ignore depends_on even if present, remain active
        - JobModule with unmet dependencies: status set to 'blocked'
        - JobModule with no dependencies or all dependencies completed: status set to 'active'

        Note: For Instances created in the same batch, dependency evaluation is based on
        whether the Instance exists in the current batch.

        Args:
            instances: InstanceDict list
        """
        # Collect all task_keys (used to determine if dependencies are in the current batch)
        all_task_keys = {inst.task_key or inst.instance_id for inst in instances}

        for inst in instances:
            # Only JobModule handles dependency relationships
            if inst.module_class != "JobModule":
                # Non-JobModule (capability-type Module): ignore depends_on, keep active
                if inst.depends_on:
                    logger.debug(f"  {inst.task_key}: Non-JobModule, ignoring depends_on, status set to active")
                    inst.depends_on = []  # Clear invalid depends_on
                if inst.status == "blocked":
                    inst.status = "active"
                continue

            # Below is the JobModule processing logic
            if not inst.depends_on:
                # No dependencies, set to active
                if inst.status == "blocked":
                    inst.status = "active"
                logger.debug(f"  {inst.task_key}: JobModule has no dependencies, status set to active")
            else:
                # Check if dependencies are all in the current batch
                has_unmet_deps = any(dep in all_task_keys for dep in inst.depends_on)
                if has_unmet_deps:
                    # Has unmet dependencies (exists in current batch), set to blocked
                    inst.status = "blocked"
                    logger.debug(f"  {inst.task_key}: JobModule has dependencies {inst.depends_on}, status set to blocked")
                else:
                    # Dependencies not in current batch (possibly historically completed), set to active
                    if inst.status == "blocked":
                        inst.status = "active"
                    logger.debug(f"  {inst.task_key}: JobModule dependencies completed, status set to active")

    async def _sync_job_to_entity(
        self,
        job_id: str,
        entity_id: str,
        agent_id: str
    ) -> None:
        """
        Sync Job ID to Social Network's Entity.related_job_ids

        Feature 2.2: Job-Entity bidirectional index
        When a Job is created, the job_id needs to be added to the target Entity's related_job_ids,
        so the target user can find associated Narratives through the Entity when querying.

        Auto-creation logic:
        - If the SocialNetworkModule instance does not exist, create it automatically
        - If the Entity does not exist, create it automatically

        Args:
            job_id: Job ID
            entity_id: Entity ID to sync to (target user ID)
            agent_id: Agent ID (used to get SocialNetwork instance_id)
        """
        try:
            from xyz_agent_context.repository import InstanceRepository, SocialNetworkRepository
            from xyz_agent_context.schema.instance_schema import ModuleInstanceRecord, InstanceStatus
            from xyz_agent_context.module import generate_instance_id

            instance_repo = InstanceRepository(self.db)
            social_repo = SocialNetworkRepository(self.db)

            # 1. Get or create SocialNetworkModule instance
            instances = await instance_repo.get_by_agent(
                agent_id=agent_id,
                module_class="SocialNetworkModule"
            )

            if not instances:
                # Auto-create SocialNetworkModule instance
                logger.info("SocialNetworkModule instance does not exist, creating automatically...")
                social_instance_id = generate_instance_id("social")
                social_instance = ModuleInstanceRecord(
                    instance_id=social_instance_id,
                    module_class="SocialNetworkModule",
                    agent_id=agent_id,
                    user_id=agent_id,  # Public instance, user_id set to agent_id
                    is_public=True,
                    status=InstanceStatus.ACTIVE,
                    description="Social network entities and relationships",
                    keywords=["social", "network", "entity", "relationship"],
                    topic_hint="Social network management",
                    created_at=utc_now(),
                )
                await instance_repo.create_instance(social_instance)
                logger.info(f"  Created SocialNetworkModule instance: {social_instance_id}")
            else:
                social_instance_id = instances[0].instance_id

            # 2. Check if Entity exists, create if it does not
            entity = await social_repo.get_entity(
                entity_id=entity_id,
                instance_id=social_instance_id
            )

            if not entity:
                # Auto-create Entity
                logger.info(f"Entity {entity_id} does not exist, creating automatically...")
                await social_repo.add_entity(
                    entity_id=entity_id,
                    entity_type="user",
                    instance_id=social_instance_id,
                    entity_name=entity_id,  # Use entity_id as default name
                    entity_description=f"Auto-created entity for {entity_id}",
                    tags=["auto-created", "job-target"],
                )
                logger.info(f"  Created Entity: {entity_id}")

            # 3. Add job_id to Entity's related_job_ids
            await social_repo.append_related_job_ids(
                entity_id=entity_id,
                instance_id=social_instance_id,
                job_ids=[job_id]
            )
            logger.info(f"  Synced Job {job_id} to Entity {entity_id}'s related_job_ids")

        except Exception as e:
            logger.error(f"  Failed to sync Job to Entity: {e}")
            import traceback
            traceback.print_exc()
            # Do not raise exception, allow Job creation to succeed even if sync fails

    async def _add_participant_to_narrative(
        self,
        narrative_id: str,
        participant_id: str
    ) -> None:
        """
        Add a user as PARTICIPANT type to the Narrative's actors list

        Feature P0-4 (2026-01-22): Sales scenario support
        The Job's target customer needs to be able to access the associated Narrative.
        When the target user sends a message, the system can match this Narrative via PARTICIPANT query.

        Args:
            narrative_id: Narrative ID
            participant_id: PARTICIPANT user ID to add
        """
        try:
            from xyz_agent_context.repository import NarrativeRepository
            from xyz_agent_context.narrative.models import NarrativeActorType, NarrativeActor

            narrative_repo = NarrativeRepository(self.db)
            narrative = await narrative_repo.get_by_id(narrative_id)

            if not narrative:
                logger.warning(f"Narrative {narrative_id} not found, skipping participant add")
                return

            # Check if already exists in actors (any type: USER, AGENT, PARTICIPANT)
            # If already a Creator (USER) or already a PARTICIPANT, no need to add again
            existing_actor = any(
                actor.id == participant_id
                for actor in narrative.narrative_info.actors
            )

            if existing_actor:
                logger.debug(
                    f"User {participant_id} already exists in Narrative {narrative_id} actors, skipping"
                )
                return

            # Add new PARTICIPANT
            new_actor = NarrativeActor(
                id=participant_id,
                type=NarrativeActorType.PARTICIPANT
            )
            narrative.narrative_info.actors.append(new_actor)

            # Save updates
            await narrative_repo.save(narrative)
            logger.info(
                f"  ✅ Added PARTICIPANT {participant_id} to Narrative {narrative_id}"
            )

        except Exception as e:
            logger.error(f"  ❌ Failed to add participant to narrative: {e}")
            # Do not raise exception, allow Job creation to succeed even if PARTICIPANT addition fails
