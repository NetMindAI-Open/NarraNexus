"""
@file_name: job_repository.py
@author: NetMind.AI
@date: 2025-12-02
@description: Job Repository - Data access layer for background tasks

Responsibilities:
- CRUD operations for Jobs
- Retrieve due tasks (with row locks to prevent concurrency)
- Semantic search and keyword search
- Task status and time management
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.utils import utc_now
from xyz_agent_context.schema.job_schema import (
    JobType,
    JobStatus,
    JobModel,
    TriggerConfig,
)


class JobRepository(BaseRepository[JobModel]):
    """
    Job Repository implementation

    Usage example:
        repo = JobRepository(db_client)

        # Create a task
        job_id = await repo.create_job(...)

        # Get a single task
        job = await repo.get_job("job_xxx")

        # Get due tasks
        due_jobs = await repo.get_due_jobs()

        # Semantic search
        results = await repo.search_semantic(agent_id, "daily news")
    """

    table_name = "instance_jobs"
    id_field = "job_id"  # Use job_id as the primary key identifier (not the auto-increment id)

    # JSON fields (2026-01-21: added monitored_job_ids)
    _json_fields = {"trigger_config", "process", "embedding", "monitored_job_ids"}

    # =========================================================================
    # Basic CRUD
    # =========================================================================

    async def get_job(self, job_id: str) -> Optional[JobModel]:
        """
        Get a single Job

        Args:
            job_id: Job ID

        Returns:
            JobModel or None
        """
        logger.debug(f"    → JobRepository.get_job({job_id})")
        return await self.find_one({"job_id": job_id})

    async def get_jobs_by_agent(
        self,
        agent_id: str,
        status: Optional[JobStatus] = None,
        job_type: Optional[JobType] = None,
        limit: int = 50
    ) -> List[JobModel]:
        """
        Get Job list for an Agent

        Args:
            agent_id: Agent ID
            status: Filter by status
            job_type: Filter by type
            limit: Maximum number of results

        Returns:
            List of JobModel
        """
        logger.debug(f"    → JobRepository.get_jobs_by_agent({agent_id})")

        filters = {"agent_id": agent_id}
        if status:
            filters["status"] = status.value
        if job_type:
            filters["job_type"] = job_type.value

        return await self.find(
            filters=filters,
            limit=limit,
            order_by="created_at DESC"
        )

    async def get_jobs_by_user(
        self,
        user_id: str,
        status: Optional[JobStatus] = None,
        limit: int = 50
    ) -> List[JobModel]:
        """
        Get Job list for a user

        Args:
            user_id: User ID
            status: Filter by status
            limit: Maximum number of results

        Returns:
            List of JobModel
        """
        logger.debug(f"    → JobRepository.get_jobs_by_user({user_id})")

        filters = {"user_id": user_id}
        if status:
            filters["status"] = status.value

        return await self.find(
            filters=filters,
            limit=limit,
            order_by="created_at DESC"
        )

    async def get_jobs_by_instance(
        self,
        instance_id: str,
        status: Optional[JobStatus] = None,
        job_type: Optional[JobType] = None,
        limit: int = 50
    ) -> List[JobModel]:
        """
        Get Job list for an Instance

        Changelog (2025-12-24):
        - Added method to query Jobs by instance_id

        Args:
            instance_id: Instance ID (instance_id of the JobModule)
            status: Filter by status
            job_type: Filter by type
            limit: Maximum number of results

        Returns:
            List of JobModel
        """
        logger.debug(f"    → JobRepository.get_jobs_by_instance({instance_id})")

        filters = {"instance_id": instance_id}
        if status:
            filters["status"] = status.value
        if job_type:
            filters["job_type"] = job_type.value

        return await self.find(
            filters=filters,
            limit=limit,
            order_by="created_at DESC"
        )

    async def create_job(
        self,
        agent_id: str,
        user_id: str,
        job_id: str,
        title: str,
        description: str,
        job_type: JobType,
        trigger_config: TriggerConfig,
        payload: str,
        instance_id: Optional[str] = None,
        notification_method: str = "inbox",
        next_run_time: Optional[datetime] = None,
        embedding: Optional[List[float]] = None,
        related_entity_id: Optional[str] = None,
        narrative_id: Optional[str] = None,
        monitored_job_ids: Optional[List[str]] = None,  # 2026-01-21: Monitor Job pattern
    ) -> int:
        """
        Create a Job

        Changelog (2025-12-24):
        - Added instance_id parameter

        Changelog (2026-01-15 Feature 2.2.1):
        - Added related_entity_id parameter, supporting Job-Entity association

        Changelog (2026-01-15 Feature 3.1):
        - Added narrative_id parameter, for loading conversation context summary

        Changelog (2026-01-21 ONGOING Job):
        - Added monitored_job_ids parameter, supporting monitor Job pattern

        Args:
            agent_id: Agent ID
            user_id: User ID
            job_id: Job ID
            title: Title
            description: Description
            job_type: Task type
            trigger_config: Trigger configuration
            payload: Execution instruction
            instance_id: Instance ID (instance_id of the JobModule)
            notification_method: Notification method
            next_run_time: Next execution time
            embedding: Semantic embedding
            related_entity_id: Target user ID (used as the principal identity when the Job executes)
            narrative_id: Associated Narrative ID (for loading conversation context)
            monitored_job_ids: Monitor Job pattern, other Job IDs monitored by this Job

        Returns:
            Inserted record ID
        """
        logger.debug(f"    → JobRepository.create_job({job_id})")

        now = utc_now()
        job = JobModel(
            job_id=job_id,
            agent_id=agent_id,
            user_id=user_id,
            instance_id=instance_id,
            title=title,
            description=description,
            job_type=job_type,
            trigger_config=trigger_config,
            payload=payload,
            status=JobStatus.PENDING,
            process=[],
            next_run_time=next_run_time,
            notification_method=notification_method,
            embedding=embedding,
            related_entity_id=related_entity_id,  # Feature 2.2.1
            narrative_id=narrative_id,  # Feature 3.1
            monitored_job_ids=monitored_job_ids,  # 2026-01-21: Monitor Job pattern
            iteration_count=0,  # 2026-01-21: ONGOING initial execution count
            created_at=now,
            updated_at=now,
        )

        return await self.insert(job)

    async def find_active_by_title(
        self,
        agent_id: str,
        user_id: str,
        title: str
    ) -> Optional[JobModel]:
        """
        Find an active Job by title (for duplicate detection)

        Only searches for Jobs with PENDING and ACTIVE status, avoiding conflicts
        with completed/failed Jobs.

        Args:
            agent_id: Agent ID
            user_id: User ID
            title: Job title

        Returns:
            Found JobModel or None
        """
        logger.debug(f"    → JobRepository.find_active_by_title({title})")

        query = f"""
            SELECT * FROM {self.table_name}
            WHERE agent_id = %s
              AND user_id = %s
              AND title = %s
              AND status IN ('pending', 'active')
            ORDER BY created_at DESC
            LIMIT 1
        """

        rows = await self._db.execute(query, params=(agent_id, user_id, title), fetch=True)
        if rows:
            return self._row_to_entity(rows[0])
        return None

    async def get_active_jobs_by_narrative(
        self,
        narrative_id: str,
        limit: int = 100
    ) -> List[JobModel]:
        """
        Get all active Jobs under a Narrative (for semantic deduplication)

        Only searches for non-terminal Jobs (pending, active, running).

        Args:
            narrative_id: Narrative ID
            limit: Maximum number of results

        Returns:
            List of JobModel
        """
        logger.debug(f"    → JobRepository.get_active_jobs_by_narrative({narrative_id})")

        query = f"""
            SELECT * FROM {self.table_name}
            WHERE narrative_id = %s
              AND status IN ('pending', 'active', 'running')
            ORDER BY created_at DESC
            LIMIT %s
        """

        rows = await self._db.execute(query, params=(narrative_id, limit), fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def get_active_jobs_by_agent(
        self,
        agent_id: str,
        limit: int = 50
    ) -> List[JobModel]:
        """
        Get all active Jobs under an Agent (for semantic deduplication)

        Only searches for non-terminal Jobs (pending, active, running).

        Args:
            agent_id: Agent ID
            limit: Maximum number of results

        Returns:
            List of JobModel
        """
        logger.debug(f"    → JobRepository.get_active_jobs_by_agent({agent_id})")

        query = f"""
            SELECT * FROM {self.table_name}
            WHERE agent_id = %s
              AND status IN ('pending', 'active', 'running')
            ORDER BY created_at DESC
            LIMIT %s
        """

        rows = await self._db.execute(query, params=(agent_id, limit), fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def update_job(
        self,
        job_id: str,
        updates: Dict[str, Any]
    ) -> int:
        """
        Update a Job

        Handles field serialization:
        - process: List -> JSON
        - status: JobStatus -> str
        - embedding: List -> JSON
        - trigger_config: TriggerConfig -> JSON

        Args:
            job_id: Job ID
            updates: Fields to update

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → JobRepository.update_job({job_id})")

        # Serialize fields that need special handling
        serialized_updates = {}
        for key, value in updates.items():
            if key == "process" and isinstance(value, list):
                serialized_updates[key] = json.dumps(value, ensure_ascii=False)
            elif key == "status" and hasattr(value, 'value'):
                serialized_updates[key] = value.value
            elif key == "embedding" and isinstance(value, list):
                serialized_updates[key] = json.dumps(value)
            elif key == "trigger_config" and hasattr(value, 'model_dump'):
                serialized_updates[key] = json.dumps(value.model_dump(mode='json'), ensure_ascii=False)
            else:
                serialized_updates[key] = value

        serialized_updates["updated_at"] = utc_now()
        return await self.update(job_id, serialized_updates)

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        error_message: Optional[str] = None
    ) -> int:
        """
        Update Job status

        Args:
            job_id: Job ID
            status: New status
            error_message: Error message

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → JobRepository.update_job_status({job_id}, {status})")

        now = utc_now()
        updates = {
            "status": status.value,
            "updated_at": now,
        }

        # Record start time when RUNNING
        if status == JobStatus.RUNNING:
            updates["started_at"] = now
        # Clear start time when not RUNNING
        elif status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ACTIVE, JobStatus.CANCELLED):
            updates["started_at"] = None

        if error_message:
            updates["last_error"] = error_message

        # Use job_id as filter condition
        query = f"""
            UPDATE {self.table_name}
            SET status = %s, updated_at = %s
            {"" if "started_at" not in updates else ", started_at = %s"}
            {"" if "last_error" not in updates else ", last_error = %s"}
            WHERE job_id = %s
        """

        params = [updates["status"], updates["updated_at"]]
        if "started_at" in updates:
            params.append(updates["started_at"])
        if "last_error" in updates:
            params.append(updates["last_error"])
        params.append(job_id)

        result = await self._db.execute(query, params=tuple(params), fetch=False)
        return result if isinstance(result, int) else 0

    async def get_jobs_by_entity_id(
        self,
        agent_id: str,
        entity_id: str,
        status: Optional[JobStatus] = None,
        limit: int = 50
    ) -> List[JobModel]:
        """
        Reverse query: find all Jobs whose related_entity_id matches the given entity_id

        Feature 2.2.1 implementation: Job-Entity reverse query capability

        Args:
            agent_id: Agent ID
            entity_id: Entity ID (single value)
            status: Optional status filter
            limit: Maximum number of results

        Returns:
            List of Jobs, sorted by updated_at descending

        Example:
            # Query all active Jobs related to user_alice
            jobs = await repo.get_jobs_by_entity_id(
                agent_id="agent_123",
                entity_id="user_alice",
                status=JobStatus.ACTIVE
            )
        """
        logger.debug(f"    → JobRepository.get_jobs_by_entity_id(entity_id={entity_id})")

        query = f"""
            SELECT * FROM {self.table_name}
            WHERE agent_id = %s
            AND related_entity_id = %s
        """
        params: List[Any] = [agent_id, entity_id]

        if status:
            query += " AND status = %s"
            params.append(status.value)

        query += " ORDER BY updated_at DESC LIMIT %s"
        params.append(limit)

        rows = await self._db.execute(query, params=tuple(params), fetch=True)

        jobs = []
        for row in rows:
            try:
                jobs.append(self._row_to_entity(row))
            except Exception as e:
                logger.error(f"Failed to parse job row: {e}")
                continue

        logger.debug(f"    → Found {len(jobs)} jobs for entity_id={entity_id}")
        return jobs

    async def update_job_fields(
        self,
        job_id: str,
        updates: Dict[str, Any]
    ) -> int:
        """
        Update specific fields of a Job (generic update method)

        Supported fields:
        - Basic info: title, description, payload
        - Scheduling config: trigger_config, job_type, next_run_time
        - Status control: status (active, paused, cancelled)
        - Association: related_entity_id

        Feature 2.2.2 implementation: underlying update capability for Type A/B/C operations

        Args:
            job_id: Job ID
            updates: Field update dictionary

        Returns:
            Number of affected rows

        Example:
            # Type A: Supplement guidance (append to payload)
            await repo.update_job_fields(
                job_id="job_abc",
                updates={"payload": "Original instruction\n\n## Supervisor supplement\nEmphasize after-sales service advantages"}
            )

            # Type B: Execute immediately
            await repo.update_job_fields(
                job_id="job_abc",
                updates={"next_run_time": utc_now()}
            )

            # Type C: Pause
            await repo.update_job_fields(
                job_id="job_abc",
                updates={"status": JobStatus.PAUSED}
            )
        """
        logger.debug(f"    → JobRepository.update_job_fields({job_id}, fields={list(updates.keys())})")

        if not updates:
            return 0

        allowed_fields = {
            'title', 'description', 'payload',
            'next_run_time', 'status', 'related_entity_id',
            'trigger_config', 'job_type'
        }

        # Filter out disallowed fields
        updates = {k: v for k, v in updates.items() if k in allowed_fields}

        if not updates:
            logger.warning(f"No valid fields to update for job {job_id}")
            return 0

        # Build update dictionary
        update_data = {}

        for key, value in updates.items():
            if key == 'status':
                # Enum field
                update_data[key] = value.value if hasattr(value, 'value') else value
            elif key == 'job_type':
                # Enum field
                update_data[key] = value.value if hasattr(value, 'value') else value
            elif key == 'next_run_time':
                # Datetime field
                update_data[key] = value
            elif key == 'trigger_config':
                # JSON field: TriggerConfig object or dict
                if hasattr(value, 'model_dump'):
                    update_data[key] = json.dumps(value.model_dump())
                elif isinstance(value, dict):
                    update_data[key] = json.dumps(value)
                else:
                    update_data[key] = value
            else:
                # Plain string field
                update_data[key] = value

        # Add automatic updated_at update
        update_data["updated_at"] = utc_now()

        # Use the base repository's update method
        affected_rows = await self._db.update(
            self.table_name,
            filters={"job_id": job_id},
            data=update_data
        )

        logger.debug(f"    → Updated {affected_rows} rows")
        return affected_rows

    async def pause_job(self, job_id: str) -> int:
        """
        Pause a Job

        Updates the status to PAUSED, and JobTrigger will no longer trigger execution.

        Feature 2.2.2 implementation: Type C pause operation

        Args:
            job_id: Job ID

        Returns:
            Number of affected rows

        Example:
            # Sales manager says: "Pause that one, wait until their internal discussion is done"
            await repo.pause_job("job_xiaoming_followup")
        """
        logger.debug(f"    → JobRepository.pause_job({job_id})")
        return await self.update_job_fields(
            job_id,
            {"status": JobStatus.PAUSED}
        )

    async def cancel_job(self, job_id: str) -> int:
        """
        Cancel a Job

        Updates the status to CANCELLED, and the Job will be terminated and never triggered again.

        Feature 2.2.2 implementation: Type C cancel operation

        Args:
            job_id: Job ID

        Returns:
            Number of affected rows

        Example:
            # Sales manager says: "Stop following up on this customer, cancel the related tasks"
            await repo.cancel_job("job_customer_followup")
        """
        logger.debug(f"    → JobRepository.cancel_job({job_id})")
        return await self.update_job_fields(
            job_id,
            {"status": JobStatus.CANCELLED}
        )

    async def try_acquire_job(self, job_id: str) -> bool:
        """
        Attempt to atomically acquire the Job execution lock

        Can only succeed when the Job's current status is PENDING or ACTIVE.
        Upon success, the status is set to RUNNING.

        This is an atomic operation to prevent multiple Workers from executing the same Job simultaneously.

        Args:
            job_id: Job ID

        Returns:
            True: Successfully acquired the lock (status updated to RUNNING)
            False: Failed to acquire (Job already locked by another Worker or not found)
        """
        logger.debug(f"    → JobRepository.try_acquire_job({job_id})")

        now = utc_now()

        # Atomic update: only update to RUNNING when status is PENDING or ACTIVE
        query = f"""
            UPDATE {self.table_name}
            SET status = %s, started_at = %s, updated_at = %s
            WHERE job_id = %s AND status IN (%s, %s)
        """

        params = (
            JobStatus.RUNNING.value,
            now,
            now,
            job_id,
            JobStatus.PENDING.value,
            JobStatus.ACTIVE.value,
        )

        result = await self._db.execute(query, params=params, fetch=False)
        affected_rows = result if isinstance(result, int) else 0

        if affected_rows > 0:
            logger.info(f"    ✓ Acquired lock for job {job_id}")
            return True
        else:
            logger.debug(f"    → Failed to acquire lock for job {job_id} (already running or not found)")
            return False

    async def delete_job(self, job_id: str) -> int:
        """
        Delete a Job

        Args:
            job_id: Job ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → JobRepository.delete_job({job_id})")

        query = f"DELETE FROM {self.table_name} WHERE job_id = %s"
        result = await self._db.execute(query, params=(job_id,), fetch=False)
        return result if isinstance(result, int) else 0

    # =========================================================================
    # Task Scheduling
    # =========================================================================

    async def get_due_jobs(self, limit: int = 100) -> List[JobModel]:
        """
        Get due tasks (with row locks to prevent concurrency)

        Uses FOR UPDATE SKIP LOCKED to ensure multiple JobTrigger instances
        do not execute the same task

        Args:
            limit: Maximum number of results

        Returns:
            List of JobModel ready for execution
        """
        logger.debug("    → JobRepository.get_due_jobs()")

        query = f"""
            SELECT * FROM {self.table_name}
            WHERE next_run_time <= %s
            AND status IN (%s, %s)
            ORDER BY next_run_time ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """

        results = await self._db.execute(
            query,
            params=(utc_now(), JobStatus.PENDING.value, JobStatus.ACTIVE.value, limit),
            fetch=True
        )

        return [self._row_to_entity(row) for row in results]

    async def recover_stuck_jobs(self, timeout_minutes: int = 30) -> int:
        """
        Recover stuck tasks

        Fix (2026-01-22): Also update next_run_time during recovery to avoid immediate re-triggering

        Args:
            timeout_minutes: Timeout duration (minutes)

        Returns:
            Number of recovered tasks
        """
        logger.debug(f"    → JobRepository.recover_stuck_jobs({timeout_minutes})")

        timeout_threshold = utc_now() - timedelta(minutes=timeout_minutes)

        # Query timed-out RUNNING tasks (including trigger_config for calculating next_run_time)
        query = f"""
            SELECT job_id, job_type, trigger_config FROM {self.table_name}
            WHERE status = %s
            AND started_at IS NOT NULL
            AND started_at < %s
        """

        results = await self._db.execute(
            query,
            params=(JobStatus.RUNNING.value, timeout_threshold),
            fetch=True
        )

        if not results:
            return 0

        recovered_count = 0
        now = utc_now()

        for row in results:
            job_id = row["job_id"]
            job_type_str = row["job_type"]

            # Determine recovery status based on type
            new_status = JobStatus.PENDING if job_type_str == JobType.ONE_OFF.value else JobStatus.ACTIVE

            # Parse trigger_config and calculate new next_run_time
            trigger_config_raw = row.get("trigger_config")
            next_run_time = None

            if job_type_str in (JobType.SCHEDULED.value, JobType.ONGOING.value):
                try:
                    # Parse trigger_config
                    trigger_config = self._parse_json_field(trigger_config_raw, {})
                    if trigger_config:
                        tc = TriggerConfig(**trigger_config) if isinstance(trigger_config, dict) else trigger_config
                        job_type_enum = JobType(job_type_str)
                        # Calculate next execution time (based on current time + interval)
                        next_run_time = calculate_next_run_time(
                            job_type=job_type_enum,
                            trigger_config=tc,
                            last_run_time=now
                        )
                except Exception as e:
                    logger.warning(f"Failed to calculate next_run_time for {job_id}: {e}")
                    # Fallback: set to 1 hour later
                    next_run_time = now + timedelta(hours=1)

            # Build update statement
            if next_run_time:
                update_query = f"""
                    UPDATE {self.table_name}
                    SET status = %s, started_at = NULL, next_run_time = %s,
                        last_error = %s, updated_at = %s
                    WHERE job_id = %s
                """
                params = (
                    new_status.value,
                    next_run_time,
                    f"Task timeout after {timeout_minutes} minutes, auto-recovered at {now}",
                    now,
                    job_id
                )
            else:
                update_query = f"""
                    UPDATE {self.table_name}
                    SET status = %s, started_at = NULL,
                        last_error = %s, updated_at = %s
                    WHERE job_id = %s
                """
                params = (
                    new_status.value,
                    f"Task timeout after {timeout_minutes} minutes, auto-recovered at {now}",
                    now,
                    job_id
                )

            await self._db.execute(update_query, params=params, fetch=False)

            next_run_str = next_run_time.strftime("%Y-%m-%d %H:%M") if next_run_time else "N/A"
            logger.warning(f"Recovered stuck job: {job_id} -> {new_status.value}, next_run: {next_run_str}")
            recovered_count += 1

        return recovered_count

    async def recover_all_running_jobs(self) -> int:
        """
        Recover all running-state tasks on startup (no timeout restriction)

        A new process startup implies that previous executions were interrupted,
        so all running jobs are unconditionally recovered.
        Unlike recover_stuck_jobs, this does not check started_at timeout.

        Returns:
            Number of recovered tasks
        """
        logger.info("    → JobRepository.recover_all_running_jobs() [startup recovery]")

        query = f"""
            SELECT job_id, job_type, trigger_config FROM {self.table_name}
            WHERE status = %s
        """

        results = await self._db.execute(
            query,
            params=(JobStatus.RUNNING.value,),
            fetch=True
        )

        if not results:
            return 0

        recovered_count = 0
        now = utc_now()

        for row in results:
            job_id = row["job_id"]
            job_type_str = row["job_type"]

            # Determine recovery status based on type
            new_status = JobStatus.PENDING if job_type_str == JobType.ONE_OFF.value else JobStatus.ACTIVE

            # Set next_run_time to current time so the next poll cycle schedules it immediately
            # Since the last execution was interrupted, it should be re-executed as soon as possible
            update_query = f"""
                UPDATE {self.table_name}
                SET status = %s, started_at = NULL, next_run_time = %s,
                    last_error = %s, updated_at = %s
                WHERE job_id = %s
            """
            params = (
                new_status.value,
                now,
                f"Process restarted, auto-recovered at {now}",
                now,
                job_id
            )

            await self._db.execute(update_query, params=params, fetch=False)
            logger.warning(f"Startup recovery: {job_id} -> {new_status.value}, next_run: NOW (immediate execution)")
            recovered_count += 1

        return recovered_count

    async def update_next_run_time(
        self,
        job_id: str,
        next_run_time: Optional[datetime],
        last_run_time: Optional[datetime] = None
    ) -> int:
        """
        Update next execution time

        Args:
            job_id: Job ID
            next_run_time: Next execution time
            last_run_time: Last execution time

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → JobRepository.update_next_run_time({job_id})")

        updates = ["next_run_time = %s", "updated_at = %s"]
        params = [next_run_time, utc_now()]

        if last_run_time:
            updates.append("last_run_time = %s")
            params.append(last_run_time)

        params.append(job_id)

        query = f"""
            UPDATE {self.table_name}
            SET {', '.join(updates)}
            WHERE job_id = %s
        """

        result = await self._db.execute(query, params=tuple(params), fetch=False)
        return result if isinstance(result, int) else 0

    async def update_next_run_time_by_instance(
        self,
        instance_id: str,
        next_run_time: datetime
    ) -> int:
        """
        Update Job's next_run_time by instance_id

        Used to activate BLOCKED Jobs after dependencies are fulfilled,
        making them pollable by JobTrigger.

        Args:
            instance_id: Instance ID
            next_run_time: Next execution time

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → JobRepository.update_next_run_time_by_instance({instance_id})")

        query = f"""
            UPDATE {self.table_name}
            SET next_run_time = %s, updated_at = %s
            WHERE instance_id = %s AND status IN (%s, %s)
        """

        result = await self._db.execute(
            query,
            params=(
                next_run_time,
                utc_now(),
                instance_id,
                JobStatus.PENDING.value,
                JobStatus.ACTIVE.value
            ),
            fetch=False
        )
        return result if isinstance(result, int) else 0

    async def add_event_to_process(self, job_id: str, event_id: str) -> int:
        """
        Add event_id to the process list

        Args:
            job_id: Job ID
            event_id: Event ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → JobRepository.add_event_to_process({job_id}, {event_id})")

        now = utc_now()
        query = f"""
            UPDATE {self.table_name}
            SET process = JSON_ARRAY_APPEND(process, '$', %s),
                last_run_time = %s,
                updated_at = %s
            WHERE job_id = %s
        """

        result = await self._db.execute(
            query,
            params=(event_id, now, now, job_id),
            fetch=False
        )
        return result if isinstance(result, int) else 0

    # =========================================================================
    # Search Features
    # =========================================================================

    async def search_semantic(
        self,
        agent_id: str,
        query_embedding: List[float],
        user_id: Optional[str] = None,
        status: Optional[JobStatus] = None,
        limit: int = 10,
        min_similarity: float = 0.3
    ) -> List[Tuple[JobModel, float]]:
        """
        Semantic search for tasks

        Args:
            agent_id: Agent ID
            query_embedding: Query embedding vector
            user_id: Filter by User ID
            status: Filter by status
            limit: Maximum number of results
            min_similarity: Minimum similarity threshold

        Returns:
            List of (JobModel, similarity_score) tuples
        """
        logger.debug(f"    → JobRepository.search_semantic({agent_id})")

        filters = {"agent_id": agent_id}
        if user_id:
            filters["user_id"] = user_id
        if status:
            filters["status"] = status.value

        results = await self._db.semantic_search(
            table=self.table_name,
            embedding_column="embedding",
            query_embedding=query_embedding,
            filters=filters,
            limit=limit,
            min_similarity=min_similarity,
        )

        return [(self._row_to_entity(row), score) for row, score in results]

    async def search_by_keywords(
        self,
        agent_id: str,
        keywords: List[str],
        user_id: Optional[str] = None,
        status: Optional[JobStatus] = None,
        limit: int = 20
    ) -> List[JobModel]:
        """
        Keyword search for tasks

        Args:
            agent_id: Agent ID
            keywords: List of keywords
            user_id: Filter by User ID
            status: Filter by status
            limit: Maximum number of results

        Returns:
            List of JobModel
        """
        logger.debug(f"    → JobRepository.search_by_keywords({agent_id}, {keywords})")

        if not keywords:
            return []

        conditions = ["agent_id = %s"]
        params: List[Any] = [agent_id]

        if user_id:
            conditions.append("user_id = %s")
            params.append(user_id)

        if status:
            conditions.append("status = %s")
            params.append(status.value)

        # Keyword matching
        keyword_conditions = []
        for keyword in keywords:
            keyword_conditions.append(
                "(title LIKE %s OR description LIKE %s OR payload LIKE %s)"
            )
            pattern = f"%{keyword}%"
            params.extend([pattern, pattern, pattern])

        if keyword_conditions:
            conditions.append(f"({' OR '.join(keyword_conditions)})")

        query = f"""
            SELECT * FROM {self.table_name}
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT {limit}
        """

        results = await self._db.execute(query, params=tuple(params), fetch=True)
        return [self._row_to_entity(row) for row in results]

    async def get_active_jobs_summary(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get active task summary

        Args:
            agent_id: Agent ID
            user_id: User ID
            limit: Maximum number of results

        Returns:
            List of task summaries
        """
        logger.debug(f"    → JobRepository.get_active_jobs_summary({agent_id}, {user_id})")

        query = f"""
            SELECT job_id, title, next_run_time, job_type, status
            FROM {self.table_name}
            WHERE agent_id = %s AND user_id = %s
            AND status IN (%s, %s)
            ORDER BY next_run_time ASC
            LIMIT %s
        """

        results = await self._db.execute(
            query,
            params=(agent_id, user_id, JobStatus.PENDING.value, JobStatus.ACTIVE.value, limit),
            fetch=True
        )

        summaries = []
        for row in results:
            next_run = row.get("next_run_time")
            next_run_str = next_run.strftime("%Y-%m-%d %H:%M") if next_run else "N/A"

            summaries.append({
                "job_id": row["job_id"],
                "title": row["title"],
                "next_run_time": next_run_str,
                "job_type": row["job_type"],
                "status": row["status"],
            })

        return summaries

    # =========================================================================
    # Conversion Methods
    # =========================================================================

    def _row_to_entity(self, row: Dict[str, Any]) -> JobModel:
        """
        Convert a database row to a JobModel object

        Changelog (2026-01-20 Feature 2.2.1):
        - Added related_entity_id field (single value, not a list)

        Changelog (2026-01-16 Feature 3.1):
        - Added narrative_id field parsing

        Changelog (2026-01-21 ONGOING Job):
        - Added monitored_job_ids and iteration_count field parsing
        """
        # Parse JSON fields
        trigger_config_data = self._parse_json_field(row.get("trigger_config"), {})
        process = self._parse_json_field(row.get("process"), [])
        embedding = self._parse_json_field(row.get("embedding"), None)
        monitored_job_ids = self._parse_json_field(row.get("monitored_job_ids"), None)

        # Rebuild TriggerConfig (handling double serialization case)
        if isinstance(trigger_config_data, str):
            # If the parsed result is still a string, try parsing again
            try:
                trigger_config_data = json.loads(trigger_config_data)
            except (json.JSONDecodeError, TypeError):
                trigger_config_data = {}

        trigger_config = TriggerConfig(**trigger_config_data) if isinstance(trigger_config_data, dict) else TriggerConfig()

        return JobModel(
            id=row.get("id"),
            job_id=row["job_id"],
            agent_id=row["agent_id"],
            user_id=row["user_id"],
            instance_id=row.get("instance_id"),
            title=row["title"],
            description=row.get("description", ""),
            job_type=JobType(row["job_type"]),
            trigger_config=trigger_config,
            payload=row.get("payload", ""),
            status=JobStatus(row["status"]) if row.get("status") else JobStatus.PENDING,
            process=process,
            next_run_time=row.get("next_run_time"),
            last_run_time=row.get("last_run_time"),
            started_at=row.get("started_at"),
            notification_method=row.get("notification_method", "inbox"),
            last_error=row.get("last_error"),
            embedding=embedding,
            related_entity_id=row.get("related_entity_id"),  # Feature 2.2.1 (single value)
            narrative_id=row.get("narrative_id"),  # Feature 3.1
            monitored_job_ids=monitored_job_ids,  # 2026-01-21: Monitor Job pattern
            iteration_count=row.get("iteration_count", 0),  # 2026-01-21: ONGOING execution count
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: JobModel) -> Dict[str, Any]:
        """
        Convert a JobModel object to a database row

        Changelog (2026-01-20 Feature 2.2.1):
        - Added related_entity_id field (single value, not a list)

        Changelog (2026-01-16 Feature 3.1):
        - Added narrative_id field serialization

        Changelog (2026-01-21 ONGOING Job):
        - Added monitored_job_ids and iteration_count field serialization
        """
        return {
            "job_id": entity.job_id,
            "agent_id": entity.agent_id,
            "user_id": entity.user_id,
            "instance_id": entity.instance_id,
            "title": entity.title,
            "description": entity.description,
            "job_type": entity.job_type.value,
            "trigger_config": json.dumps(entity.trigger_config.model_dump(mode='json'), ensure_ascii=False),
            "payload": entity.payload,
            "status": entity.status.value,
            "process": json.dumps(entity.process, ensure_ascii=False),
            "next_run_time": entity.next_run_time,
            "last_run_time": entity.last_run_time,
            "started_at": entity.started_at,
            "notification_method": entity.notification_method,
            "last_error": entity.last_error,
            "embedding": json.dumps(entity.embedding) if entity.embedding else None,
            "related_entity_id": entity.related_entity_id,  # Feature 2.2.1 (single value)
            "narrative_id": entity.narrative_id,  # Feature 3.1
            "monitored_job_ids": json.dumps(entity.monitored_job_ids) if entity.monitored_job_ids else None,  # 2026-01-21
            "iteration_count": entity.iteration_count,  # 2026-01-21
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

    @staticmethod
    def _parse_json_field(value: Any, default: Any) -> Any:
        """
        Parse a JSON field (supports multi-level serialization)

        Handles the following cases:
        - None -> default
        - Already a list/dict -> return directly
        - JSON string -> parse and return
        - Double-serialized string -> parse recursively
        """
        if value is None:
            return default

        # If already the target type, return directly
        if isinstance(value, (list, dict)):
            return value

        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                # If the parsed result is still a string, it may be double-serialized; handle recursively
                if isinstance(parsed, str):
                    return JobRepository._parse_json_field(parsed, default)
                return parsed
            except json.JSONDecodeError:
                return default

        return value


def calculate_next_run_time(*args, **kwargs):
    """Re-export shim (moved to _job_scheduling.py). Lazy import to avoid circular dependency."""
    from xyz_agent_context.module.job_module._job_scheduling import calculate_next_run_time as _impl
    return _impl(*args, **kwargs)
