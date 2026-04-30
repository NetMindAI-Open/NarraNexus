"""
@file_name: _job_mcp_tools.py
@author: NetMind.AI
@date: 2025-11-25
@description: JobModule MCP Server tool definitions

Separates MCP tool registration logic from the JobModule main class,
keeping JobModule focused on Hook and core business logic.

Tools:
- job_create: Create a background job
- job_retrieval_semantic: Semantic search for jobs
- job_retrieval_by_id: Query job by ID
- job_retrieval_by_keywords: Keyword search for jobs
- job_update: Update job properties
- job_pause: Pause a job
- job_cancel: Cancel a job
"""

from typing import Optional, List, Any

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.repository import JobRepository
from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding
from xyz_agent_context.agent_framework.api_config import setup_mcp_llm_context, LLMConfigNotConfigured


def create_job_mcp_server(port: int, get_db_client_fn) -> FastMCP:
    """
    Create a JobModule MCP Server instance

    Args:
        port: MCP Server port
        get_db_client_fn: Async function to get database connection (JobModule.get_mcp_db_client)

    Returns:
        FastMCP instance with all tools configured
    """
    mcp = FastMCP("job_module")
    mcp.settings.port = port

    # -----------------------------------------------------------------
    # Tool: job_create
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_create(
        agent_id: str,
        user_id: str,
        title: str,
        description: str,
        job_type: str,
        trigger_config: dict,
        payload: str,
        notification_method: str = "direct",
        task_key: Optional[str] = None,
        depends_on_job_ids: Optional[List[str]] = None,
        related_entity_id: Optional[str] = None,
        narrative_id: Optional[str] = None
    ) -> dict:
        """
        Create a background Job - USE SPARINGLY, CHECK IF I ALREADY CREATED JOBS FIRST!

        ⚠️ IMPORTANT: Before calling this tool, check the "Jobs I Just Created" section
        in my instructions. I may have already created jobs for the user's request.
        Only use this tool for NEW scheduled/recurring tasks not already created.

        When to use this tool (RARE):
        - User explicitly requests a NEW recurring reminder (e.g., "Remind me to drink water every day at 8am")
        - User requests a NEW one-time scheduled task not covered by existing jobs
        - NO matching job exists in the jobs I already created

        When NOT to use this tool:
        - Any task that matches a job I already created
        - Multi-step workflows (I already created job chains for these)
        - Research/analysis tasks (I already created jobs for these)

        Args:
            agent_id: The Agent ID that owns this job
            user_id: The User ID who created/requested this job
            title: Short title for the job
            description: Detailed description
            job_type: Job type - MUST choose the right type:
                - "one_off": Single execution at a specific time
                - "scheduled": Periodic execution on a schedule (cron or interval)
                - "ongoing": Continuous execution until end_condition is met (REQUIRED for sales follow-up!)
            trigger_config: Configuration depends on job_type.
                REQUIRED for every shape: "timezone" as an IANA name
                (e.g. "Asia/Shanghai", "America/New_York"). Use the user's
                current timezone from the User Temporal Context.
                - one_off: {"run_at": "2026-01-20T09:00:00", "timezone": "Asia/Shanghai"}
                    NOTE: run_at MUST be naive ISO 8601 (no "Z", no "+08:00" suffix).
                    Declare timezone via the separate "timezone" field instead.
                - scheduled: {"cron": "0 8 * * *", "timezone": "Asia/Shanghai"}
                             or {"interval_seconds": 3600, "timezone": "Asia/Shanghai"}
                - ongoing: {"interval_seconds": 86400, "end_condition": "...", "timezone": "Asia/Shanghai"}
            payload: The instruction to execute
            notification_method: default "inbox"
            task_key: Optional identifier for dependencies
            depends_on_job_ids: Optional list of job instance_ids to wait for
            related_entity_id: Target user ID for this job. IMPORTANT rules:
                - If job is for Agent to work and report back to requester: put requester's user_id
                  Example: User asks "research competitors" → "user_requester_id"
                - If job involves acting on another user (sales, notifications): put target user's ID
                  Example: Manager says "sell to xiaoming" → "user_xiaoming"
                - This ID will be used as the main identity when job executes
            narrative_id: Narrative ID to load conversation context/summary during execution

        Returns:
            dict with success, job_id, instance_id, message

        Examples:
            # Self-service job (Agent works, reports back to requester)
            job_create(
                agent_id="agent_123",
                user_id="user_manager",
                title="Competitor Research",
                description="Research main competitors",
                job_type="one_off",
                trigger_config={"run_at": "2026-01-20T09:00:00", "timezone": "Asia/Shanghai"},
                payload="Research competitors and send report...",
                related_entity_id="user_manager"  # Report back to requester
            )

            # ONGOING job for sales follow-up (IMPORTANT: Use this for sales tasks!)
            job_create(
                agent_id="agent_123",
                user_id="user_manager",  # Manager who assigned the task
                title="Sell MacBook Air M4 to Xiaoming",
                description="Continuously follow up with customer Xiaoming to sell MacBook Air M4",
                job_type="ongoing",  # ← MUST be "ongoing" for sales follow-up!
                trigger_config={
                    "interval_seconds": 86400,  # Check every day
                    "end_condition": "Customer explicitly closes deal (places order) or explicitly declines (says not needed)",
                    "timezone": "Asia/Shanghai"
                },
                payload="Target customer Xiaoming, sell MacBook Air M4, understand needs and recommend suitable configuration",
                related_entity_id="user_xiaoming",  # Target customer
                narrative_id="nar_xxx"  # Link to sales project narrative
            )

            # Scheduled job for daily progress report
            job_create(
                agent_id="agent_123",
                user_id="user_manager",
                title="MacBook Air Sales Progress Monitoring",
                description="Report customer follow-up progress to sales manager daily",
                job_type="scheduled",
                trigger_config={"cron": "0 18 * * *", "timezone": "Asia/Shanghai"},  # 6 PM daily
                payload="Report follow-up progress for all customers, report regardless of whether there is progress",
                related_entity_id="user_manager"  # Report to manager
            )
        """
        from xyz_agent_context.module.job_module.job_service import JobInstanceService

        await setup_mcp_llm_context(agent_id)
        db = await get_db_client_fn()
        service = JobInstanceService(db)
        result = await service.create_job_with_instance(
            agent_id=agent_id,
            user_id=user_id,
            title=title,
            description=description,
            job_type=job_type,
            trigger_config=trigger_config,
            payload=payload,
            notification_method=notification_method,
            dependencies=depends_on_job_ids,
            related_entity_id=related_entity_id,
            narrative_id=narrative_id
        )

        if result.get("success") and task_key:
            result["task_key"] = task_key

        return result

    # -----------------------------------------------------------------
    # Tool: job_retrieval_semantic
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_retrieval_semantic(
        agent_id: str,
        query: str,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 10,
    ) -> dict:
        """
        Search jobs using natural language semantic similarity.

        Use this tool when you need to find jobs based on meaning rather
        than exact keyword matches. The search understands context and
        finds related jobs even if the exact words don't match.

        Args:
            agent_id: Your Agent ID (required)
            query: Natural language search query describing what you're looking for
            user_id: Optional filter by user ID
            status: Optional filter by status. Valid values:
                - "pending": Waiting for first trigger
                - "active": Active (scheduled/ongoing job running normally)
                - "running": Currently executing
                - "paused": Paused by manager
                - "completed": Finished (one_off completed, or ongoing reached end_condition)
                - "failed": Execution failed
                - "cancelled": Cancelled by manager
            limit: Maximum number of results (default: 10)

        Returns:
            dict with success status and list of matching jobs with similarity scores

        Examples:
            # Find news-related jobs
            job_retrieval_semantic(
                agent_id="agent_xxx",
                query="daily news updates and summaries"
            )

            # Find reminder tasks for a specific user
            job_retrieval_semantic(
                agent_id="agent_xxx",
                query="meeting reminders",
                user_id="user_123",
                status="active"
            )
        """
        try:
            status_enum = None
            if status:
                try:
                    status_enum = JobStatus(status.lower())
                except ValueError:
                    return {
                        "success": False,
                        "error": f"Invalid status: {status}. Valid values: pending, active, running, completed, failed"
                    }

            await setup_mcp_llm_context(agent_id)
            db = await get_db_client_fn()
            repo = JobRepository(db)

            query_embedding = await get_embedding(query)

            results = await repo.search_semantic(
                agent_id=agent_id,
                query_embedding=query_embedding,
                user_id=user_id,
                status=status_enum,
                limit=limit
            )

            from xyz_agent_context.module.job_module._job_response import job_to_llm_dict
            jobs_data = [
                {**job_to_llm_dict(job), "similarity_score": round(score, 4)}
                for job, score in results
            ]

            return {
                "success": True,
                "query": query,
                "total_results": len(jobs_data),
                "jobs": jobs_data,
            }

        except Exception as e:
            logger.exception(f"Error in job_retrieval_semantic: {e}")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # Tool: job_retrieval_by_id
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_retrieval_by_id(
        agent_id: str,
        job_id: str
    ) -> dict:
        """
        Retrieve a specific job by its ID.

        Use this tool when you know the exact job_id and need to get
        its full details.

        Args:
            agent_id: Your Agent ID (required for verification)
            job_id: The exact job ID to retrieve (e.g., "job_abc123")

        Returns:
            dict with success status and complete job details

        Example:
            job_retrieval_by_id(
                agent_id="agent_xxx",
                job_id="job_abc123"
            )
        """
        try:
            db = await get_db_client_fn()
            repo = JobRepository(db)
            job = await repo.get_job(job_id)

            if not job:
                return {"success": False, "error": f"Job not found: {job_id}"}

            if job.agent_id != agent_id:
                return {"success": False, "error": "Access denied: Job belongs to a different agent"}

            from xyz_agent_context.module.job_module._job_response import job_to_llm_dict
            return {
                "success": True,
                "job": {
                    **job_to_llm_dict(job),
                    "process": job.process,
                    "last_error": job.last_error,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "updated_at": job.updated_at.isoformat() if job.updated_at else None,
                },
            }

        except Exception as e:
            logger.exception(f"Error in job_retrieval_by_id: {e}")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # Tool: job_retrieval_by_keywords
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_retrieval_by_keywords(
        agent_id: str,
        keywords: List[str],
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """
        Search jobs by keyword matching.

        Use this tool for simple keyword-based search when you know
        specific terms that appear in job titles, descriptions, or payloads.

        Args:
            agent_id: Your Agent ID (required)
            keywords: List of keywords to search for (matches if ANY keyword found)
            user_id: Optional filter by user ID
            status: Optional filter by status (pending/active/running/paused/completed/failed/cancelled)
            limit: Maximum number of results (default: 20)

        Returns:
            dict with success status and list of matching jobs

        Example:
            job_retrieval_by_keywords(
                agent_id="agent_xxx",
                keywords=["news", "summary"],
                status="active"
            )
        """
        try:
            status_enum = None
            if status:
                try:
                    status_enum = JobStatus(status.lower())
                except ValueError:
                    return {"success": False, "error": f"Invalid status: {status}"}

            db = await get_db_client_fn()
            repo = JobRepository(db)
            jobs = await repo.search_by_keywords(
                agent_id=agent_id,
                keywords=keywords,
                user_id=user_id,
                status=status_enum,
                limit=limit
            )

            from xyz_agent_context.module.job_module._job_response import job_to_llm_dict
            jobs_data = []
            for job in jobs:
                entry = job_to_llm_dict(job)
                if len(entry["description"] or "") > 200:
                    entry["description"] = entry["description"][:200] + "..."
                jobs_data.append(entry)

            return {
                "success": True,
                "keywords": keywords,
                "total_results": len(jobs_data),
                "jobs": jobs_data,
            }

        except Exception as e:
            logger.exception(f"Error in job_retrieval_by_keywords: {e}")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # Tool: job_update (Feature 2.2.2)
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_update(
        agent_id: str,
        job_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        payload: Optional[str] = None,
        guidance_text: Optional[str] = None,
        trigger_config: Optional[dict] = None,
        job_type: Optional[str] = None,
        next_run_time: Optional[str] = None,
        status: Optional[str] = None,
        related_entity_id: Optional[str] = None
    ) -> dict:
        """
        Update an existing Job's properties, scheduling, or status.

        WHEN TO USE THIS TOOL:
        - Manager requests to modify a job (e.g., "Change follow-up frequency to weekly")
        - Manager provides additional guidance for a job (e.g., "Emphasize after-sales service when following up with Xiaoming")
        - Manager wants to pause/resume/cancel a job (e.g., "Pause follow-up with Xiaoming")
        - Manager wants to reschedule a job (e.g., "Execute this task immediately")
        - Need to change job type (e.g., from one_off to ongoing)

        WHEN NOT TO USE THIS TOOL:
        - Creating a new job → use job_create instead
        - Querying job details → use job_retrieval_by_id instead
        - The job doesn't exist yet

        ARGS:
            agent_id (str, required): Your Agent ID. Used for authorization check.

            job_id (str, required): The ID of the job to update. Format: "job_xxxxxxxx".
                You can get this from job_retrieval_* tools or from the jobs I already created.

            title (str, optional): New title for the job.
                Example: "Sell MacBook Pro M4 to Xiaoming"

            description (str, optional): New description for the job.
                Example: "Continuously follow up with customer Xiaoming to sell new MacBook Pro"

            payload (str, optional): New execution instruction. REPLACES the entire existing payload.
                Use this when you need to completely rewrite the job's instructions.
                Example: "Contact Xiaoming, understand his interest in MacBook Pro, focus on M4 chip performance"

            guidance_text (str, optional): Additional guidance to APPEND to the existing payload.
                This adds a "## Manager Guidance" section at the end of the payload.
                Use this when manager provides supplementary instructions without replacing the original.
                Example: "Emphasize our 24-hour after-sales service and free on-site repair"
                Note: If both payload and guidance_text are provided, guidance_text appends to the new payload.

            trigger_config (dict, optional): New trigger configuration.
                REQUIRED for every shape: "timezone" as an IANA name (e.g.
                "Asia/Shanghai"). Use the user's current timezone from the
                User Temporal Context. Examples:
                - For ONE_OFF jobs:
                    {"run_at": "2026-01-20T09:00:00", "timezone": "Asia/Shanghai"}
                    NOTE: run_at MUST be naive ISO 8601 (no "Z" / no offset).
                - For SCHEDULED jobs (choose one):
                    {"cron": "0 8 * * *", "timezone": "Asia/Shanghai"}
                    {"cron": "0 9 * * 1", "timezone": "America/New_York"}
                    {"interval_seconds": 3600, "timezone": "Asia/Shanghai"}
                    {"interval_seconds": 86400, "timezone": "Asia/Shanghai"}
                - For ONGOING jobs:
                    {"interval_seconds": 86400, "end_condition": "...", "timezone": "Asia/Shanghai"}
                    {"interval_seconds": 172800, "end_condition": "...", "max_iterations": 10, "timezone": "Asia/Shanghai"}
                Common intervals: 3600(1h), 86400(1d), 172800(2d), 604800(1w)

            job_type (str, optional): Change the job type. Valid values:
                - "one_off": Execute once at a specific time, then complete
                - "scheduled": Execute repeatedly on a schedule (cron or interval)
                - "ongoing": Execute repeatedly until end_condition is met
                WARNING: When changing job_type, you should also update trigger_config accordingly.

            next_run_time (str, optional): Override the next execution time.
                ISO8601 UTC format ("YYYY-MM-DDTHH:MM:SSZ" or with explicit offset).
                Use this for Type-B "execute immediately" or "reschedule to X".
                For regular schedule changes, prefer updating trigger_config so the
                job's frozen timezone is re-applied automatically.

            status (str, optional): Change job status. Valid values:
                - "active": Activate or resume the job. JobTrigger will poll and execute it.
                - "paused": Pause the job. JobTrigger will skip it. Can be resumed later.
                - "cancelled": Cancel permanently. This is a TERMINAL state - cannot be undone.
                Note: Use job_pause tool for simple pause, job_cancel for simple cancel.

            related_entity_id (str, optional): Update the target entity (user) for this job.
                This determines who the job is "about" or "for".
                Example: "user_xiaoming" for a sales follow-up job targeting xiaoming.

        RETURNS:
            dict with keys:
            - success (bool): Whether the update succeeded
            - job_id (str): The job ID that was updated
            - updated_fields (list): List of field names that were updated
            - message (str): Success or error message

        EXAMPLES:
            # Manager says: "Change Xiaoming's follow-up task to weekly"
            job_update(
                agent_id="agent_123",
                job_id="job_abc123",
                trigger_config={"interval_seconds": 604800, "timezone": "Asia/Shanghai"}  # 7 days
            )

            # Manager says: "Emphasize after-sales service advantages when following up with Xiaoming"
            job_update(
                agent_id="agent_123",
                job_id="job_abc123",
                guidance_text="Focus on emphasizing our 24-hour after-sales service and 3-year warranty policy"
            )

            # Manager says: "Execute this task right now"
            job_update(
                agent_id="agent_123",
                job_id="job_abc123",
                next_run_time="2026-01-15T10:30:00"  # current time
            )

            # Manager says: "Pause follow-up with Xiaoming, wait until their internal discussion is done"
            job_update(agent_id="agent_123", job_id="job_abc123", status="paused")

            # Manager says: "Xiaoming already bought from another vendor, cancel this task"
            job_update(agent_id="agent_123", job_id="job_abc123", status="cancelled")

            # Change a one-off job to ongoing with end condition
            job_update(
                agent_id="agent_123",
                job_id="job_abc123",
                job_type="ongoing",
                trigger_config={"interval_seconds": 86400, "end_condition": "Customer completes purchase or explicitly refuses", "timezone": "Asia/Shanghai"}
            )
        """
        try:
            from xyz_agent_context.module.job_module.job_service import JobInstanceService
            from xyz_agent_context.schema.job_schema import JobType
            from datetime import datetime

            db = await get_db_client_fn()
            job_repo = JobRepository(db)

            # Verify authorization
            job = await job_repo.get_job(job_id)
            if not job:
                return {"success": False, "job_id": job_id, "message": f"Job {job_id} not found"}

            if job.agent_id != agent_id:
                return {"success": False, "job_id": job_id, "message": f"Job {job_id} does not belong to agent {agent_id}"}

            # Build update dictionary
            updates = {}

            if title is not None:
                updates["title"] = title
            if description is not None:
                updates["description"] = description
            if payload is not None:
                updates["payload"] = payload
            if guidance_text:
                base_payload = updates.get("payload", job.payload) or ""
                updates["payload"] = f"{base_payload}\n\n## Manager Guidance\n{guidance_text}"
            if trigger_config is not None:
                # Validate + recompute alpha/beta atomically so display matches poller view
                from xyz_agent_context.schema.job_schema import TriggerConfig
                from xyz_agent_context.module.job_module._job_scheduling import compute_next_run
                from pydantic import ValidationError as _VE
                try:
                    tc_model = TriggerConfig(**trigger_config)
                except _VE as ve:
                    first = ve.errors()[0]
                    loc = ".".join(str(p) for p in first.get("loc", ()))
                    return {"success": False, "job_id": job_id,
                            "message": f"Invalid trigger_config ({loc}): {first['msg']}"}
                updates["trigger_config"] = tc_model
                effective_type = updates.get("job_type", job.job_type)
                nxt = compute_next_run(effective_type, tc_model)
                if nxt:
                    updates["next_run_time"] = nxt.utc
                    updates["next_run_at_local"] = nxt.local
                    updates["next_run_tz"] = nxt.tz
                else:
                    updates["next_run_time"] = None
                    updates["next_run_at_local"] = None
                    updates["next_run_tz"] = None
            if job_type is not None:
                try:
                    updates["job_type"] = JobType(job_type.lower())
                except ValueError:
                    return {"success": False, "job_id": job_id, "message": f"Invalid job_type: {job_type}. Valid: one_off, scheduled, ongoing"}
            if next_run_time is not None:
                # Atomic alpha+beta override: parse UTC input, then derive the
                # beta pair in the job's frozen timezone so display and poller
                # stay consistent.
                try:
                    next_utc = datetime.fromisoformat(next_run_time.replace("Z", "+00:00"))
                    if next_utc.tzinfo is None:
                        from datetime import timezone as _tz
                        next_utc = next_utc.replace(tzinfo=_tz.utc)
                except ValueError as e:
                    return {"success": False, "job_id": job_id, "message": f"Invalid next_run_time format: {e}"}
                from zoneinfo import ZoneInfo
                tz_name = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
                next_local = next_utc.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
                updates["next_run_time"] = next_utc
                updates["next_run_at_local"] = next_local
                updates["next_run_tz"] = tz_name
            if status is not None:
                try:
                    updates["status"] = JobStatus(status.lower())
                except ValueError:
                    return {"success": False, "job_id": job_id, "message": f"Invalid status: {status}. Valid: active, paused, cancelled"}
            if related_entity_id is not None:
                updates["related_entity_id"] = related_entity_id

            if not updates:
                return {"success": False, "job_id": job_id, "message": "No fields to update"}

            service = JobInstanceService(db)
            return await service.update_job(job_id=job_id, updates=updates, agent_id=agent_id)

        except Exception as e:
            logger.exception(f"Error in job_update: {e}")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # Tool: job_pause (Feature 2.2.2)
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_pause(
        agent_id: str,
        job_id: str
    ) -> dict:
        """
        Pause a Job (Feature 2.2.2 - Type C Operation)

        Set job status to PAUSED. The job will not be triggered by JobTrigger until resumed.

        Use case:
            Sales manager says: "Wait on contacting Alice until they finish their internal discussion"

        Args:
            agent_id: Agent ID (for authorization)
            job_id: Job ID to pause

        Returns:
            dict with success status and message

        Example:
            job_pause(
                agent_id="agent_123",
                job_id="job_xiaoming_followup"
            )
        """
        try:
            db = await get_db_client_fn()
            job_repo = JobRepository(db)

            job = await job_repo.get_job(job_id)
            if not job:
                return {"success": False, "job_id": job_id, "message": f"Job {job_id} not found"}
            if job.agent_id != agent_id:
                return {"success": False, "job_id": job_id, "message": f"Job {job_id} does not belong to agent {agent_id}"}

            updated_rows = await job_repo.pause_job(job_id)

            return {
                "success": updated_rows > 0,
                "job_id": job_id,
                "status": "paused",
                "message": "Job paused successfully" if updated_rows > 0 else "Failed to pause job"
            }

        except Exception as e:
            logger.exception(f"Error in job_pause: {e}")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------
    # Tool: job_cancel (Feature 2.2.2)
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_cancel(
        agent_id: str,
        job_id: str
    ) -> dict:
        """
        Cancel a Job and clean up entity associations (Feature 2.2.2 - Type C Operation)

        Set job status to CANCELLED and remove from all related entities' related_job_ids.

        **Important**: This is a terminal operation. Cancelled jobs cannot be resumed.

        Use case:
            Sales manager says: "We're no longer following up with this customer, cancel all related tasks"

        Args:
            agent_id: Agent ID (for authorization)
            job_id: Job ID to cancel

        Returns:
            dict with success status and message

        Example:
            job_cancel(
                agent_id="agent_123",
                job_id="job_customer_followup"
            )
        """
        try:
            from xyz_agent_context.repository import SocialNetworkRepository
            from xyz_agent_context.module.job_module.job_service import JobInstanceService

            db = await get_db_client_fn()
            job_repo = JobRepository(db)

            job = await job_repo.get_job(job_id)
            if not job:
                return {"success": False, "job_id": job_id, "message": f"Job {job_id} not found"}
            if job.agent_id != agent_id:
                return {"success": False, "job_id": job_id, "message": f"Job {job_id} does not belong to agent {agent_id}"}

            # 1. Cancel Job
            updated_rows = await job_repo.cancel_job(job_id)

            # 2. Clean up Entity associations
            if job.related_entity_id:
                service = JobInstanceService(db)
                social_instance_id = await service._get_social_network_instance_id(agent_id)
                if social_instance_id:
                    social_repo = SocialNetworkRepository(db)
                    try:
                        await social_repo.remove_related_job_ids(
                            entity_id=job.related_entity_id,
                            instance_id=social_instance_id,
                            job_ids=[job_id]
                        )
                    except Exception as e:
                        logger.exception(f"Failed to remove job {job_id} from entity {job.related_entity_id}: {e}")

            return {
                "success": updated_rows > 0,
                "job_id": job_id,
                "status": "cancelled",
                "message": "Job cancelled successfully" if updated_rows > 0 else "Failed to cancel job"
            }

        except Exception as e:
            logger.exception(f"Error in job_cancel: {e}")
            return {"success": False, "error": str(e)}

    return mcp
