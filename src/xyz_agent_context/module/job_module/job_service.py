"""
@file_name: job_service.py
@author: NetMind.AI
@date: 2025-12-25
@description: Unified Job creation service

Provides a unified entry point for creating Job Instances and Job records, used by:
1. Instance Decision: LLM plans multiple dependent Jobs
2. MCP Tool: Agent calls job_create during conversation

Design notes:
- Unified creation of ModuleInstance and Job records
- Supports dependency relationships
- Generates embedding vectors
"""

from __future__ import annotations

from typing import Optional, Dict, Any, List, TYPE_CHECKING
from uuid import uuid4

from loguru import logger
from xyz_agent_context.utils import utc_now

if TYPE_CHECKING:
    from xyz_agent_context.utils import DatabaseClient
    from xyz_agent_context.schema.job_schema import JobModel


class JobInstanceService:
    """
    Unified Job Instance creation service

    Responsibilities:
    1. Create ModuleInstance records
    2. Create Job records
    3. Generate embedding vectors
    4. Handle dependency relationships

    Use cases:
    1. MCP Tool (job_create)
    2. Instance Decision (step_2_5)
    """

    def __init__(self, database_client: "DatabaseClient"):
        """
        Initialize the service

        Args:
            database_client: Database client
        """
        self.db = database_client

    async def create_job_with_instance(
        self,
        agent_id: str,
        user_id: str,
        title: str,
        description: str,
        job_type: str,
        trigger_config: Dict[str, Any],
        payload: str,
        notification_method: str = "inbox",
        dependencies: Optional[List[str]] = None,
        instance_id: Optional[str] = None,
        related_entity_id: Optional[str] = None,  # Feature 2.2.1 (changed to single value)
        narrative_id: Optional[str] = None,  # Feature 3.1
        monitored_job_ids: Optional[List[str]] = None,  # Monitored Job pattern (2026-01-21)
    ) -> Dict[str, Any]:
        """
        Create a Job and its corresponding ModuleInstance

        This is the unified creation entry point that simultaneously creates:
        1. ModuleInstance record (module_instances table)
        2. Job record (jobs table)
        3. Syncs to Social Network Entity (if related_entity_id is provided)

        Enhancement notes (2026-01-20 Feature 2.2.1):
        - Added related_entity_id parameter, target user ID (used as the primary identity during Job execution)
        - Syncs to Social Network after creation (bidirectional index)

        Enhancement notes (2026-01-15 Feature 3.1):
        - Added narrative_id parameter for associating conversation context

        Enhancement notes (2026-01-21 ONGOING Job):
        - Supports job_type="ongoing", continuous execution until end condition is met
        - Added monitored_job_ids parameter for monitored Job pattern

        Args:
            agent_id: Agent ID
            user_id: User ID
            title: Job title
            description: Job description
            job_type: Job type ("one_off", "scheduled", or "ongoing")
            trigger_config: Trigger configuration
            payload: Execution instruction
            notification_method: Notification method
            dependencies: List of dependent instance_ids
            instance_id: Specified instance_id (optional, auto-generated if not provided)
            related_entity_id: Target user ID, used as the primary identity during Job execution
            narrative_id: Associated Narrative ID (for loading conversation context)
            monitored_job_ids: Monitored Job pattern, the Job IDs monitored by this Job

        Returns:
            Dict with success status, job_id, instance_id, and message
        """
        from xyz_agent_context.schema.job_schema import JobType, TriggerConfig, JobStatus
        from xyz_agent_context.repository import JobRepository, InstanceRepository, NarrativeRepository
        from xyz_agent_context.repository.job_repository import calculate_next_run_time
        from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding, prepare_job_text_for_embedding
        from xyz_agent_context.schema.instance_schema import ModuleInstanceRecord, InstanceStatus
        from xyz_agent_context.narrative.models import NarrativeActorType, NarrativeActor

        try:
            # 0. Creator permission check: if narrative_id is specified, check if current user is Creator
            if narrative_id:
                narrative_repo = NarrativeRepository(self.db)
                narrative = await narrative_repo.get_by_id(narrative_id)
                if narrative:
                    is_creator = any(
                        actor.id == user_id and actor.type == NarrativeActorType.USER
                        for actor in narrative.narrative_info.actors
                    )
                    if not is_creator:
                        logger.warning(
                            f"User {user_id} is not the creator of Narrative {narrative_id}"
                        )
                        return {
                            "success": False,
                            "error": "Only Narrative creator can create Job in this Narrative"
                        }

            # 0.5. Duplicate detection: check if an active Job with the same title already exists
            job_repo = JobRepository(self.db)
            existing_job = await job_repo.find_active_by_title(agent_id, user_id, title)
            if existing_job:
                logger.info(f"Found existing active job with same title: {existing_job.job_id}")
                return {
                    "success": True,
                    "job_id": existing_job.job_id,
                    "instance_id": existing_job.instance_id,
                    "message": f"Job '{title}' already exists and is active. Returning existing job.",
                    "is_existing": True  # Mark this as an existing Job, not newly created
                }

            # 0.6. Semantic similarity detection: check if a semantically similar active Job already exists
            # This prevents Agent Loop from creating duplicate tasks that Instance Decision already created
            active_jobs = await job_repo.get_active_jobs_by_agent(agent_id, limit=50)
            if active_jobs:
                similar_job = self._find_similar_job_by_title(title, active_jobs)
                if similar_job:
                    logger.warning(
                        f"Found semantically similar active job: '{similar_job.title}' (ID: {similar_job.job_id})"
                    )
                    return {
                        "success": True,
                        "job_id": similar_job.job_id,
                        "instance_id": similar_job.instance_id,
                        "message": (
                            f"A similar job '{similar_job.title}' already exists and is active. "
                            f"Returning existing job instead of creating duplicate."
                        ),
                        "is_existing": True,
                        "similar_match": True  # Mark this as a semantic similarity match
                    }

            # 1. Validate job_type
            try:
                job_type_enum = JobType(job_type.lower())
            except ValueError:
                return {
                    "success": False,
                    "error": f"Invalid job_type: {job_type}. Must be 'one_off', 'scheduled', or 'ongoing'"
                }

            # 2. Parse trigger_config
            trigger = TriggerConfig(**trigger_config)

            # 3. Validate trigger_config
            if job_type_enum == JobType.ONE_OFF and not trigger.run_at:
                return {
                    "success": False,
                    "error": "one_off job requires 'run_at' in trigger_config"
                }

            if job_type_enum == JobType.SCHEDULED:
                if not trigger.cron and not trigger.interval_seconds:
                    return {
                        "success": False,
                        "error": "scheduled job requires 'cron' or 'interval_seconds' in trigger_config"
                    }

            # 3.5. ONGOING type validation (added 2026-01-21)
            if job_type_enum == JobType.ONGOING:
                if not trigger.interval_seconds:
                    return {
                        "success": False,
                        "error": "ongoing job requires 'interval_seconds' in trigger_config"
                    }
                if not trigger.end_condition and not trigger.max_iterations:
                    return {
                        "success": False,
                        "error": "ongoing job requires 'end_condition' or 'max_iterations' in trigger_config"
                    }

            # 4. Generate IDs
            if not instance_id:
                instance_id = f"job_{uuid4().hex[:8]}"
            job_id = f"job_{uuid4().hex[:12]}"

            # 5. Calculate next_run_time
            next_run_time = calculate_next_run_time(job_type_enum, trigger)

            # 6. Generate embedding vector
            embedding_text = prepare_job_text_for_embedding(title, description, payload)
            embedding = await get_embedding(embedding_text)
            # Dual-write to embeddings_store
            from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import store_embedding
            await store_embedding("job", job_id, embedding, source_text=embedding_text)

            # 7. Determine initial status
            initial_status = InstanceStatus.ACTIVE
            if dependencies:
                # Has dependencies, check if it needs to be set to BLOCKED
                # Simplified handling: if there are dependencies, set to BLOCKED
                initial_status = InstanceStatus.BLOCKED

            # 8. Create ModuleInstance record
            instance_repo = InstanceRepository(self.db)
            instance_record = ModuleInstanceRecord(
                instance_id=instance_id,
                module_class="JobModule",
                agent_id=agent_id,
                user_id=user_id,
                is_public=False,
                status=initial_status,
                description=description,
                dependencies=dependencies or [],
                config={
                    "job_id": job_id,
                    "title": title,
                },
                routing_embedding=embedding,
                topic_hint=title,
            )
            await instance_repo.create_instance(instance_record)
            logger.info(f"Created ModuleInstance: {instance_id}")

            # 9. Create Job record (reusing job_repo from step 0)
            await job_repo.create_job(
                agent_id=agent_id,
                user_id=user_id,
                job_id=job_id,
                title=title,
                description=description,
                job_type=job_type_enum,
                trigger_config=trigger,
                payload=payload,
                instance_id=instance_id,
                notification_method=notification_method,
                next_run_time=next_run_time,
                embedding=embedding,
                related_entity_id=related_entity_id,  # Feature 2.2.1 (single value)
                narrative_id=narrative_id,  # Feature 3.1
                monitored_job_ids=monitored_job_ids,  # 2026-01-21: Monitored Job pattern
            )
            logger.info(f"Created Job: {job_id}")

            # 10. Sync to Social Network (Feature 2.2.1)
            if related_entity_id:
                try:
                    await self._sync_job_to_entity(
                        job_id=job_id,
                        entity_id=related_entity_id,
                        agent_id=agent_id
                    )
                    logger.info(f"Synced job {job_id} to entity {related_entity_id}")
                except Exception as e:
                    # Don't interrupt creation flow, only log the error
                    logger.error(f"Failed to sync job to entities: {e}")

            # 11. Add related_entity_id as PARTICIPANT to Narrative actors (2026-01-21)
            if related_entity_id and narrative_id:
                try:
                    await self._add_participant_to_narrative(
                        narrative_id=narrative_id,
                        participant_id=related_entity_id
                    )
                    logger.info(
                        f"Added {related_entity_id} as PARTICIPANT to Narrative {narrative_id}"
                    )
                except Exception as e:
                    # Don't interrupt creation flow, only log the error
                    logger.error(f"Failed to add participant to narrative: {e}")

            return {
                "success": True,
                "job_id": job_id,
                "instance_id": instance_id,
                "message": f"Job '{title}' created successfully. It will be executed according to the schedule."
            }

        except Exception as e:
            logger.error(f"Error creating job: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def create_jobs_batch(
        self,
        agent_id: str,
        user_id: str,
        jobs_config: List[Dict[str, Any]],
        key_to_id: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Batch create multiple Jobs with dependency relationships

        Args:
            agent_id: Agent ID
            user_id: User ID
            jobs_config: List of Job configurations, each containing:
                - task_key: Semantic identifier
                - title: Title
                - description: Description
                - job_type: Type
                - trigger_config: Trigger configuration
                - payload: Execution instruction
                - depends_on: List of dependent task_keys
            key_to_id: task_key -> instance_id mapping (optional, auto-generated if not provided)

        Returns:
            Dict with created job_ids and instance_ids
        """
        # 1. Build key_to_id mapping
        if key_to_id is None:
            key_to_id = {}
            for config in jobs_config:
                task_key = config.get("task_key", "")
                if task_key and task_key not in key_to_id:
                    key_to_id[task_key] = f"job_{uuid4().hex[:8]}"

        # 2. Topological sort (ensure dependencies are created first)
        sorted_configs = self._topological_sort(jobs_config)

        # 3. Create one by one
        created_jobs = []
        created_instances = []
        errors = []

        for config in sorted_configs:
            task_key = config.get("task_key", "")
            instance_id = key_to_id.get(task_key)

            # Convert depends_on to dependencies
            depends_on = config.get("depends_on", [])
            dependencies = [key_to_id.get(dep) for dep in depends_on if dep in key_to_id]

            result = await self.create_job_with_instance(
                agent_id=agent_id,
                user_id=user_id,
                title=config.get("title", ""),
                description=config.get("description", ""),
                job_type=config.get("job_type", "one_off"),
                trigger_config=config.get("trigger_config", {}),
                payload=config.get("payload", ""),
                dependencies=dependencies,
                instance_id=instance_id,
            )

            if result["success"]:
                created_jobs.append(result["job_id"])
                created_instances.append(result["instance_id"])
            else:
                errors.append({
                    "task_key": task_key,
                    "error": result.get("error", "Unknown error")
                })

        return {
            "success": len(errors) == 0,
            "created_jobs": created_jobs,
            "created_instances": created_instances,
            "errors": errors,
            "key_to_id": key_to_id,
        }

    def _topological_sort(self, jobs_config: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Topological sort to ensure dependent Jobs are created first

        Args:
            jobs_config: List of Job configurations

        Returns:
            Sorted configuration list
        """
        # Build dependency graph
        graph: Dict[str, List[str]] = {}
        config_map: Dict[str, Dict[str, Any]] = {}

        for config in jobs_config:
            task_key = config.get("task_key", "")
            if task_key:
                graph[task_key] = config.get("depends_on", [])
                config_map[task_key] = config

        # Kahn's algorithm
        in_degree: Dict[str, int] = {key: 0 for key in graph}
        for deps in graph.values():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        # Find nodes with in-degree 0 (no dependencies)
        queue = [key for key, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            key = queue.pop(0)
            if key in config_map:
                result.append(config_map[key])

            for deps in graph.values():
                if key in deps:
                    # This is reversed, needs reconsideration
                    pass

        # Simplified: sort directly by dependency count
        result = sorted(
            jobs_config,
            key=lambda x: len(x.get("depends_on", []))
        )

        return result

    # =========================================================================
    # Feature 2.2 Methods: Job Update & Entity Sync
    # =========================================================================

    async def _sync_job_to_entity(
        self,
        job_id: str,
        entity_id: str,
        agent_id: str
    ) -> None:
        """
        Sync Job ID to Social Network Entity.related_job_ids

        Feature 2.2.1 implementation: Job-side sync logic for Job-Entity bidirectional index

        Args:
            job_id: Job ID
            entity_id: Entity ID to sync (single)
            agent_id: Agent ID (used to get SocialNetwork instance_id)
        """
        try:
            # Get SocialNetworkModule's instance_id
            social_instance_id = await self._get_social_network_instance_id(agent_id)

            if not social_instance_id:
                logger.warning(
                    f"No SocialNetworkModule instance found for agent {agent_id}, "
                    f"skipping entity sync"
                )
                return

            # Call SocialNetworkRepository (direct implementation)
            from xyz_agent_context.repository import SocialNetworkRepository

            social_repo = SocialNetworkRepository(self.db)

            try:
                await social_repo.append_related_job_ids(
                    entity_id=entity_id,
                    instance_id=social_instance_id,
                    job_ids=[job_id]
                )
                logger.debug(f"Synced job {job_id} to entity {entity_id}")
            except Exception as e:
                logger.error(
                    f"Failed to sync job {job_id} to entity {entity_id}: {e}"
                )

        except Exception as e:
            logger.error(f"Failed to sync job to entity: {e}")
            # Don't raise exception, allow Job creation to succeed even if sync fails

    async def _get_social_network_instance_id(self, agent_id: str) -> Optional[str]:
        """
        Get the Agent's SocialNetworkModule instance_id

        Args:
            agent_id: Agent ID

        Returns:
            SocialNetworkModule's instance_id, or None if it doesn't exist
        """
        from xyz_agent_context.repository import InstanceRepository

        instance_repo = InstanceRepository(self.db)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )
        return instances[0].instance_id if instances else None

    def _find_similar_job_by_title(
        self,
        new_title: str,
        existing_jobs: List["JobModel"]
    ) -> Optional["JobModel"]:
        """
        Find a semantically similar Job in the existing Job list by title

        Uses a simple string similarity algorithm (Jaccard similarity) to avoid calling the embedding API.
        If similarity exceeds the threshold (0.6), it is considered a duplicate Job.

        Args:
            new_title: Title of the new Job
            existing_jobs: List of existing active Jobs

        Returns:
            The similar Job found, or None if none found
        """
        if not existing_jobs:
            return None

        # Normalize title: remove common words, lowercase, tokenize
        def normalize_title(title: str) -> set:
            # Remove common stopwords
            stopwords = {
                "to", "the", "a", "an", "for",
                "job", "task"
            }
            # Lowercase and tokenize (simple split by spaces and common delimiters)
            import re
            words = set(re.split(r'[\s\-_,，。、]+', title.lower()))
            return words - stopwords

        new_words = normalize_title(new_title)
        if not new_words:
            return None

        best_match = None
        best_similarity = 0.0
        threshold = 0.5  # Similarity threshold

        for job in existing_jobs:
            existing_words = normalize_title(job.title)
            if not existing_words:
                continue

            # Calculate Jaccard similarity
            intersection = len(new_words & existing_words)
            union = len(new_words | existing_words)
            similarity = intersection / union if union > 0 else 0

            if similarity > best_similarity:
                best_similarity = similarity
                best_match = job

        if best_similarity >= threshold:
            logger.info(
                f"Found similar job: '{best_match.title}' (similarity={best_similarity:.2f}) "
                f"for new title: '{new_title}'"
            )
            return best_match

        return None

    async def update_job(
        self,
        job_id: str,
        updates: Dict[str, Any],
        agent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update Job fields

        Special handling:
        - If updates contains "append_to_payload", appends content to existing payload (Type A operation)
        - If related_entity_id is updated, performs diff sync to Social Network

        Feature 2.2.2 implementation: High-level interface for Type A/B/C update operations

        Args:
            job_id: Job ID
            updates: Dictionary of fields to update, supports special keys:
                - "append_to_payload": Content to append to payload (Type A)
                - Other standard fields: title, description, payload, next_run_time, status, related_entity_id
            agent_id: Agent ID (for permission verification and entity sync)

        Returns:
            {
                "success": bool,
                "job_id": str,
                "updated_fields": List[str],
                "message": str
            }

        Example:
            # Type A: Supplementary guidance (auto-appends to payload)
            await service.update_job(
                job_id,
                {"append_to_payload": "Emphasize after-sales service advantages"}
            )

            # Type B: Execute immediately
            await service.update_job(job_id, {"next_run_time": utc_now()})

            # Type C: Pause
            await service.update_job(job_id, {"status": JobStatus.PAUSED})
        """
        from xyz_agent_context.repository import JobRepository

        try:
            job_repo = JobRepository(self.db)

            # Special handling: Type A operation - append to payload
            if "append_to_payload" in updates:
                # Read current Job
                job = await job_repo.get_job(job_id)
                if not job:
                    return {
                        "success": False,
                        "job_id": job_id,
                        "message": f"Job {job_id} not found"
                    }

                # Append content to payload
                append_content = updates.pop("append_to_payload")
                original_payload = job.payload or ""
                new_payload = f"{original_payload}\n\n## Manager Supplementary Guidance\n{append_content}"
                updates["payload"] = new_payload

            # If related_entity_id was updated, need diff sync
            if "related_entity_id" in updates and agent_id:
                # 1. Get old Job info
                old_job = await job_repo.get_job(job_id)
                if not old_job:
                    return {
                        "success": False,
                        "job_id": job_id,
                        "message": f"Job {job_id} not found"
                    }

                old_entity_id = old_job.related_entity_id
                new_entity_id = updates["related_entity_id"]

                # 2. Update Job fields
                updated_rows = await job_repo.update_job_fields(job_id, updates)

                # 3. Diff sync to Social Network (if changed)
                if old_entity_id != new_entity_id:
                    await self._diff_sync_entity(
                        job_id=job_id,
                        agent_id=agent_id,
                        old_entity_id=old_entity_id,
                        new_entity_id=new_entity_id
                    )
            else:
                # Normal update, no sync needed
                updated_rows = await job_repo.update_job_fields(job_id, updates)

            return {
                "success": updated_rows > 0,
                "job_id": job_id,
                "updated_fields": list(updates.keys()),
                "message": (
                    "Job updated successfully" if updated_rows > 0
                    else "No changes made"
                )
            }

        except Exception as e:
            logger.error(f"Failed to update job {job_id}: {e}")
            return {
                "success": False,
                "job_id": job_id,
                "message": f"Failed to update job: {str(e)}"
            }

    async def _add_participant_to_narrative(
        self,
        narrative_id: str,
        participant_id: str
    ) -> None:
        """
        Add a user as PARTICIPANT type to the Narrative's actors list

        Used in sales scenarios: The Job's target customer needs to access the associated Narrative,
        but is not the Narrative's creator.

        Args:
            narrative_id: Narrative ID
            participant_id: PARTICIPANT user ID to add
        """
        from xyz_agent_context.repository import NarrativeRepository
        from xyz_agent_context.narrative.models import NarrativeActorType, NarrativeActor

        narrative_repo = NarrativeRepository(self.db)
        narrative = await narrative_repo.get_by_id(narrative_id)

        if not narrative:
            logger.warning(f"Narrative {narrative_id} not found, skipping participant add")
            return

        # Check if already exists in actors (any type: USER, AGENT, PARTICIPANT)
        # If already Creator (USER) or already PARTICIPANT, no need to add again
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

        # Save update
        await narrative_repo.save(narrative)
        logger.info(
            f"Added PARTICIPANT {participant_id} to Narrative {narrative_id}"
        )

    async def _diff_sync_entity(
        self,
        job_id: str,
        agent_id: str,
        old_entity_id: Optional[str],
        new_entity_id: Optional[str]
    ) -> None:
        """
        Diff sync: incrementally update Entity.related_job_ids based on old/new entity_id

        Args:
            job_id: Job ID
            agent_id: Agent ID
            old_entity_id: Old associated Entity ID (may be None)
            new_entity_id: New associated Entity ID (may be None)
        """
        try:
            social_instance_id = await self._get_social_network_instance_id(agent_id)
            if not social_instance_id:
                logger.warning(
                    "No SocialNetworkModule instance found, skipping diff sync"
                )
                return

            from xyz_agent_context.repository import SocialNetworkRepository

            social_repo = SocialNetworkRepository(self.db)

            # Remove old association
            if old_entity_id:
                try:
                    await social_repo.remove_related_job_ids(
                        entity_id=old_entity_id,
                        instance_id=social_instance_id,
                        job_ids=[job_id]
                    )
                    logger.debug(f"Removed job {job_id} from entity {old_entity_id}")
                except Exception as e:
                    logger.error(
                        f"Failed to remove job {job_id} from entity {old_entity_id}: {e}"
                    )

            # Add new association
            if new_entity_id:
                try:
                    await social_repo.append_related_job_ids(
                        entity_id=new_entity_id,
                        instance_id=social_instance_id,
                        job_ids=[job_id]
                    )
                    logger.debug(f"Added job {job_id} to entity {new_entity_id}")
                except Exception as e:
                    logger.error(
                        f"Failed to add job {job_id} to entity {new_entity_id}: {e}"
                    )

        except Exception as e:
            logger.error(f"Failed to diff sync entity: {e}")
