"""
@file_name: jobs.py
@author: NetMind.AI
@date: 2025-11-28
@description: REST API routes for jobs

Provides endpoints for:
- GET /api/jobs - List jobs for an agent/user
- GET /api/jobs/{job_id} - Get job details
- PUT /api/jobs/{job_id}/cancel - Cancel a job
- POST /api/jobs/complex - Create batch jobs with dependencies (Job Complex)

Refactoring notes (2025-12-24):
- Retrieve data from instance_jobs table

Refactoring notes (2026-01-04):
- Added Job Complex batch creation API
"""

import json
from typing import Optional, Any, List
from uuid import uuid4
from pydantic import BaseModel
from fastapi import APIRouter, Query
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import utc_now, format_for_api
from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema import (
    JobStatus,
    JobResponse,
    JobListResponse,
    JobDetailResponse,
)


class CancelJobResponse(BaseModel):
    """Response model for cancel job"""
    success: bool
    job_id: Optional[str] = None
    previous_status: Optional[str] = None
    error: Optional[str] = None


class JobComplexJobRequest(BaseModel):
    """Creation request for a single Job"""
    task_key: str  # Task identifier (used for dependency references)
    title: str
    description: Optional[str] = None
    depends_on: List[str] = []  # List of dependent task_keys
    payload: Optional[str] = None


class CreateJobComplexRequest(BaseModel):
    """Request to create a Job Complex"""
    agent_id: str
    user_id: str
    group_id: Optional[str] = None  # Optional group ID
    jobs: List[JobComplexJobRequest]


class CreateJobComplexResponse(BaseModel):
    """Response for creating a Job Complex"""
    success: bool
    group_id: Optional[str] = None
    jobs_created: int = 0
    job_ids: List[str] = []
    error: Optional[str] = None


router = APIRouter()


def _parse_json(value: Any, default: Any) -> Any:
    """Parse JSON field"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def job_row_to_response(row: dict, depends_on: List[str] = None) -> JobResponse:
    """
    Convert instance_jobs row to JobResponse

    Args:
        row: Database row data
        depends_on: List of dependent instance_ids (retrieved from module_instances table)
    """
    # Parse JSON fields
    trigger_config_raw = row.get("trigger_config")
    process_raw = row.get("process")

    # Recursively parse JSON (handle double-encoding issues)
    def parse_json_recursive(value, expected_type, default):
        """Recursively parse JSON until the expected type is obtained"""
        if isinstance(value, expected_type):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                # Continue recursive parsing
                return parse_json_recursive(parsed, expected_type, default)
            except (json.JSONDecodeError, TypeError):
                return default
        return default

    trigger_config = parse_json_recursive(trigger_config_raw, dict, {})
    process = parse_json_recursive(process_raw, list, [])

    return JobResponse(
        job_id=row.get("job_id"),
        agent_id=row.get("agent_id"),
        user_id=row.get("user_id"),
        job_type=row.get("job_type", "one_off"),
        title=row.get("title", ""),
        description=row.get("description", ""),
        status=row.get("status", "pending"),
        payload=row.get("payload"),
        trigger_config=trigger_config,
        process=process,
        # Use format_for_api to return ISO 8601 UTC format (with Z suffix)
        # Ensure frontend JavaScript new Date() correctly recognizes it as UTC time
        next_run_time=format_for_api(row.get("next_run_time")),
        last_run_time=format_for_api(row.get("last_run_time")),
        last_error=row.get("last_error"),
        notification_method=row.get("notification_method"),
        created_at=format_for_api(row.get("created_at")),
        updated_at=format_for_api(row.get("updated_at")),
        # New fields
        instance_id=row.get("instance_id"),
        depends_on=depends_on or [],
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    agent_id: str = Query(..., description="Agent ID"),
    user_id: Optional[str] = Query(None, description="Optional user ID filter"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(50, description="Max number of jobs to return"),
):
    """
    List jobs for an agent

    Retrieves data from instance_jobs table and dependency relationships from module_instances table
    """
    logger.info(f"Listing jobs for agent: {agent_id}, user: {user_id}, status: {status}")

    try:
        db_client = await get_db_client()

        # Build filter conditions
        filters = {"agent_id": agent_id}
        if user_id:
            filters["user_id"] = user_id
        if status:
            # Validate status value
            valid_statuses = ["pending", "active", "running", "completed", "failed", "blocked", "cancelled"]
            if status not in valid_statuses:
                return JobListResponse(
                    success=False,
                    error=f"Invalid status: {status}. Valid values: {valid_statuses}"
                )
            filters["status"] = status

        # Get data from instance_jobs table
        jobs_data = await db_client.get(
            "instance_jobs",
            filters=filters,
            order_by="created_at DESC",
            limit=limit
        )

        # Collect all instance_ids, batch query dependency relationships
        instance_ids = [row.get("instance_id") for row in jobs_data if row.get("instance_id")]

        # Batch fetch dependency relationships from module_instances table (using get_by_ids to avoid IN query issues)
        instance_deps_map: dict[str, List[str]] = {}
        if instance_ids:
            instances_data = await db_client.get_by_ids(
                "module_instances",
                "instance_id",
                instance_ids
            )
            for inst in instances_data:
                inst_id = inst.get("instance_id")
                deps_raw = inst.get("dependencies")
                # Parse dependencies (may be JSON string or list)
                if deps_raw:
                    if isinstance(deps_raw, str):
                        try:
                            deps = json.loads(deps_raw)
                        except json.JSONDecodeError:
                            deps = []
                    elif isinstance(deps_raw, list):
                        deps = deps_raw
                    else:
                        deps = []
                    instance_deps_map[inst_id] = deps

        # Convert to response format (including dependency relationships)
        job_responses = []
        for row in jobs_data:
            instance_id = row.get("instance_id")
            depends_on = instance_deps_map.get(instance_id, [])
            job_responses.append(job_row_to_response(row, depends_on))

        logger.info(f"Found {len(job_responses)} jobs")

        return JobListResponse(
            success=True,
            jobs=job_responses,
            count=len(job_responses),
        )

    except Exception as e:
        logger.error(f"Error listing jobs: {e}")
        return JobListResponse(
            success=False,
            error=str(e)
        )


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job_details(job_id: str):
    """
    Get job details by ID

    Retrieves data from instance_jobs table
    """
    logger.info(f"Getting job details: {job_id}")

    try:
        db_client = await get_db_client()

        # Get data from instance_jobs table
        job_data = await db_client.get_one(
            "instance_jobs",
            filters={"job_id": job_id}
        )

        if job_data:
            return JobDetailResponse(
                success=True,
                job=job_row_to_response(job_data),
            )
        else:
            return JobDetailResponse(
                success=False,
                error=f"Job not found: {job_id}"
            )

    except Exception as e:
        logger.error(f"Error getting job details: {e}")
        return JobDetailResponse(
            success=False,
            error=str(e)
        )


@router.put("/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_job(job_id: str):
    """
    Cancel a Job

    Sets the Job status to cancelled so it will no longer be polled for execution by JobTrigger.
    Only Jobs in pending or active status can be cancelled.
    Jobs in running status cannot be interrupted, but will be marked as cancelled and will not be re-executed.
    """
    logger.info(f"Cancel job request: {job_id}")

    try:
        db_client = await get_db_client()
        job_repo = JobRepository(db_client)

        # Get current Job status
        job = await job_repo.get_job(job_id)
        if not job:
            return CancelJobResponse(
                success=False,
                error=f"Job not found: {job_id}"
            )

        previous_status = job.status.value

        # Check if cancellation is possible
        if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            return CancelJobResponse(
                success=False,
                job_id=job_id,
                previous_status=previous_status,
                error=f"Job is already {previous_status}, cannot cancel"
            )

        # Update status to cancelled
        await job_repo.update_job_status(job_id, JobStatus.CANCELLED)

        logger.info(f"Job {job_id} cancelled successfully (was: {previous_status})")

        return CancelJobResponse(
            success=True,
            job_id=job_id,
            previous_status=previous_status,
        )

    except Exception as e:
        logger.error(f"Error cancelling job: {e}")
        return CancelJobResponse(
            success=False,
            error=str(e)
        )


@router.post("/complex", response_model=CreateJobComplexResponse)
async def create_job_complex(request: CreateJobComplexRequest):
    """
    Batch create a group of Jobs with dependency relationships (Job Complex)

    Workflow:
    1. Validate dependencies (ensure all task_keys referenced in depends_on exist)
    2. Topological sort to determine creation order
    3. Batch create Jobs, mapping task_key to actual job_id
    4. Root Jobs (no dependencies) set to ACTIVE, dependent Jobs set to PENDING

    Dependency relationships are stored in the depends_on field within payload
    """
    logger.info(f"Creating Job Complex: {len(request.jobs)} jobs")

    try:
        # 1. Validate dependencies
        task_keys = {job.task_key for job in request.jobs}
        for job in request.jobs:
            for dep in job.depends_on:
                if dep not in task_keys:
                    return CreateJobComplexResponse(
                        success=False,
                        error=f"Invalid dependency: '{dep}' not found in job list"
                    )

        # 2. Generate group_id
        group_id = request.group_id or f"group_{uuid4().hex[:8]}"

        # 3. Create Jobs via JobInstanceService (creates ModuleInstance + Job records)
        db_client = await get_db_client()
        from xyz_agent_context.module.job_module.job_service import JobInstanceService
        job_service = JobInstanceService(db_client)

        job_ids = []
        task_key_to_job_id = {}  # task_key -> job_id mapping

        for job in request.jobs:
            # Convert task_key dependencies to job_id dependencies
            depends_on_job_ids = [task_key_to_job_id[dep] for dep in job.depends_on]

            # Build payload with dependency information
            payload_str = json.dumps({
                "task_key": job.task_key,
                "depends_on": depends_on_job_ids,
                "group_id": group_id,
                "original_payload": job.payload,
            })

            result = await job_service.create_job_with_instance(
                agent_id=request.agent_id,
                user_id=request.user_id,
                title=job.title,
                description=job.description or "",
                job_type="one_off",
                trigger_config={"trigger_type": "immediate", "run_at": utc_now()},
                payload=payload_str,
                dependencies=depends_on_job_ids if depends_on_job_ids else None,
            )

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error creating job")
                logger.error(f"Failed to create job for task_key={job.task_key}: {error_msg}")
                return CreateJobComplexResponse(
                    success=False,
                    error=f"Failed to create job '{job.title}': {error_msg}"
                )

            job_id = result.get("job_id", "")
            task_key_to_job_id[job.task_key] = job_id
            job_ids.append(job_id)
            logger.info(f"Created job: {job_id} (task_key: {job.task_key}, result: {result})")

        logger.info(f"Job Complex created: group_id={group_id}, {len(job_ids)} jobs")

        return CreateJobComplexResponse(
            success=True,
            group_id=group_id,
            jobs_created=len(job_ids),
            job_ids=job_ids,
        )

    except Exception as e:
        logger.error(f"Error creating Job Complex: {e}")
        return CreateJobComplexResponse(
            success=False,
            error=str(e)
        )
