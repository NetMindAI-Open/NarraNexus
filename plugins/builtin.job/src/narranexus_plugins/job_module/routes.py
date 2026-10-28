"""
@file_name: routes.py
@author: NetMind.AI
@date: 2025-11-28
@description: REST API routes for jobs

Provides endpoints for:
- GET /api/jobs - List jobs for an agent/user
- GET /api/jobs/{job_id} - Get job details
- PUT /api/jobs/{job_id} - Update job fields (mirrors job_update MCP tool)
- PUT /api/jobs/{job_id}/cancel - Cancel a job
- PUT /api/jobs/{job_id}/pause - Pause a job (mirrors job_pause MCP tool)
- GET /api/jobs/search/semantic - BM25 search (mirrors job_retrieval_semantic MCP tool)
- GET /api/jobs/search/keywords - Keyword search (mirrors job_retrieval_by_keywords MCP tool)
- POST /api/jobs/complex - Create batch jobs with dependencies (Job Complex)

Refactoring notes (2025-12-24):
- Retrieve data from instance_jobs table

Refactoring notes (2026-01-04):
- Added Job Complex batch creation API

Refactoring notes (2026-08-10):
- Added update/pause/search-semantic/search-keywords — the backend half of the
  MCP data-access seam. Each mirrors the matching tool in
  plugins/builtin.job/src/narranexus_plugins/job_module/_job_mcp_tools.py exactly (same
  repository/service calls, same response shape) so a non-agent caller (e.g. a
  frontend panel) gets identical semantics to the agent's own tools. Gated by
  `assert_owned` — the dashboard route's pause/resume (job_recovery, status
  preconditioned) is a DIFFERENT, unrelated code path; see this file's mirror
  doc for why both exist.
"""

import json
import re
from typing import Optional, Any, List
from uuid import uuid4
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from narranexus.sdk.web import current_user_id
from narranexus.sdk.web import require_agent_owner
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils import format_for_api
from narranexus.platform.repository import JobRepository
from narranexus_plugins.job_module import (
    # aliased: the search route handlers below share these names
    search_jobs_semantic as _shared_search_semantic,
    search_jobs_by_keywords as _shared_search_keywords,
    update_job_from_args,
)
from narranexus.platform.schema import (
    JobStatus,
    JobUpdateFields,
    JobResponse,
    JobListResponse,
    JobDetailResponse,
    TriggerConfig,
)


class CancelJobResponse(BaseModel):
    """Response model for cancel job"""
    success: bool
    job_id: Optional[str] = None
    previous_status: Optional[str] = None
    error: Optional[str] = None


class JobUpdateBody(JobUpdateFields):
    """Frontend update request. Inherits the 9 mutable fields from the shared
    JobUpdateFields (declared once) and adds agent_id for ownership scoping —
    the ONLY difference from the seam route's body. Unlike the seam body it
    keeps the default extra="ignore" (no forbid): this route calls
    update_job_from_args in-process, so there is no HttpStore silent-drop path
    to guard here — the forbid guard's reason exists only on the seam leg."""
    agent_id: str


class JobUpdateResponse(BaseModel):
    """Response model for job update — same shape as JobInstanceService.update_job()."""
    success: bool
    job_id: Optional[str] = None
    updated_fields: List[str] = []
    message: Optional[str] = None


class JobPauseBody(BaseModel):
    """Pause request — mirrors job_pause MCP tool args."""
    agent_id: str


class JobPauseResponse(BaseModel):
    """Response model for job pause — same shape as the job_pause MCP tool."""
    success: bool
    job_id: str
    status: Optional[str] = None
    message: Optional[str] = None


class JobSemanticSearchResponse(BaseModel):
    """Response model for semantic (BM25) job search — same shape as job_retrieval_semantic."""
    success: bool
    query: Optional[str] = None
    total_results: int = 0
    jobs: List[dict] = []
    error: Optional[str] = None


class JobKeywordSearchResponse(BaseModel):
    """Response model for keyword job search — same shape as job_retrieval_by_keywords."""
    success: bool
    keywords: List[str] = []
    total_results: int = 0
    jobs: List[dict] = []
    error: Optional[str] = None


class JobComplexJobRequest(BaseModel):
    """Creation request for a single Job"""
    task_key: str  # Task identifier (used for dependency references)
    title: str
    description: Optional[str] = None
    depends_on: List[str] = []  # List of dependent task_keys
    payload: Optional[str] = None


class CreateJobComplexRequest(BaseModel):
    """Request to create a Job Complex.

    No user_id field: identity comes from the authenticated request, never the
    body — trusting a body-supplied user_id was exactly the create_job_complex
    IDOR. Pydantic ignores an extra user_id an old client might still send.
    """
    agent_id: str
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


def _topological_sort_job_complex(
    jobs: List["JobComplexJobRequest"],
) -> "tuple[Optional[List[JobComplexJobRequest]], Optional[str]]":
    """Kahn's algorithm over the `task_key` dependency edges (B-16).

    `create_job_complex` used to walk `body.jobs` in raw REQUEST order and
    look up `task_key_to_job_id[dep]` as it went — a forward reference (a job
    whose `depends_on` names a task_key appearing LATER in the list) raised a
    bare KeyError (#285 only sanitised the message into a generic 500, never
    fixed the ordering). This returns the jobs re-ordered so every dependency
    is created before its dependents, or `(None, error)` naming the cycle
    when the graph isn't a DAG.

    Assumes every `depends_on` entry is a known task_key AND that task_keys
    are unique — the caller validates both before this runs (a duplicate
    would silently collapse in `by_key` and drop a job; an unknown task_key
    is ignored here, not double-reported).
    """
    from collections import deque

    by_key = {j.task_key: j for j in jobs}
    indegree = {k: 0 for k in by_key}
    dependents: dict[str, List[str]] = {k: [] for k in by_key}
    for j in jobs:
        for dep in j.depends_on:
            if dep not in by_key:
                continue
            dependents[dep].append(j.task_key)
            indegree[j.task_key] += 1

    queue = deque(k for k in by_key if indegree[k] == 0)
    ordered: List[str] = []
    while queue:
        k = queue.popleft()
        ordered.append(k)
        for nxt in dependents[k]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if len(ordered) != len(by_key):
        cyclic = sorted(k for k in by_key if k not in ordered)
        return None, f"dependency cycle detected among task_keys: {', '.join(cyclic)}"

    return [by_key[k] for k in ordered], None


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


# Executor hostnames (nx-exec-<user_id>-<hash>) surface inside `last_error`
# when a run fails to reach its container. That string is internal
# infrastructure detail, not something an API response should carry, so it is
# scrubbed on the way out (SEC-02, second half of Mark's report).
_EXECUTOR_HOST_RE = re.compile(r"nx-exec-[A-Za-z0-9_-]+")


def _scrub_internal(text: Optional[str]) -> Optional[str]:
    """Redact internal executor hostnames from a user-facing error string."""
    if not text:
        return text
    return _EXECUTOR_HOST_RE.sub("nx-exec-<redacted>", text)


async def _assert_job_owner(request: Request, db_client, job_id: str) -> Optional[dict]:
    """Enforce agent-ownership for a job-id-addressed route.

    Job access is scoped to whoever owns the job's agent (``agents.created_by``,
    via the canonical helper), mirroring every other agent-scoped route. Returns
    the job row when the caller is allowed, or ``None`` when the job does not
    exist. Raises 403/404/503 from ``assert_owned`` when the caller is not the
    owner / the owning agent is unknown / the ownership lookup fails.
    """
    row = await db_client.get_one("instance_jobs", filters={"job_id": job_id})
    if not row:
        return None
    await require_agent_owner(request, row.get("agent_id"))
    return row


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
        # v2: expose user-local beta fields only; frontend renders them verbatim
        # (no Date() coercion) so the timezone label shown matches the job's
        # frozen timezone regardless of the viewer's browser timezone.
        next_run_at=row.get("next_run_at_local"),
        next_run_timezone=row.get("next_run_tz"),
        last_run_at=row.get("last_run_at_local"),
        last_run_timezone=row.get("last_run_tz"),
        last_error=_scrub_internal(row.get("last_error")),
        notification_method=row.get("notification_method"),
        created_at=format_for_api(row.get("created_at")),
        updated_at=format_for_api(row.get("updated_at")),
        # New fields
        instance_id=row.get("instance_id"),
        depends_on=depends_on or [],
        # Surface narrative_id so the bundle export wizard (P7) can group
        # jobs under their parent narrative for visual selection.
        narrative_id=row.get("narrative_id"),
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    agent_id: str = Query(..., description="Agent ID"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(50, description="Max number of jobs to return"),
):
    """
    List jobs for an agent. Identity from auth_middleware — the previous
    "optional user_id filter" let any client list anyone's jobs by
    passing a different user_id in the URL.

    Retrieves data from instance_jobs table and dependency relationships from module_instances table
    """
    user_id = await current_user_id(request)
    logger.debug(f"Listing jobs for agent: {agent_id}, user: {user_id}, status: {status}")

    try:
        db_client = await get_db_client()

        # Build filter conditions — always scoped to the caller.
        filters = {"agent_id": agent_id, "user_id": user_id}
        if status:
            # Validate status value (derive from the enum so new states —
            # paused_no_quota / cooling / blocked / blocked_failed / paused —
            # are accepted automatically).
            valid_statuses = [s.value for s in JobStatus]
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

        logger.debug(f"Found {len(job_responses)} jobs")

        return JobListResponse(
            success=True,
            jobs=job_responses,
            count=len(job_responses),
        )

    except Exception as e:
        logger.exception(f"Error listing jobs: {e}")
        return JobListResponse(
            success=False,
            error="Failed to list jobs."
        )


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job_details(job_id: str, request: Request):
    """
    Get job details by ID

    Owner-only: access is scoped to whoever owns the job's agent. Retrieves
    data from instance_jobs table.
    """
    logger.info(f"Getting job details: {job_id}")

    try:
        db_client = await get_db_client()

        # Owner gate — resolves the job's agent and denies non-owners before
        # any of its content (title/payload/last_error) is returned.
        job_data = await _assert_job_owner(request, db_client, job_id)

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

    except HTTPException:
        # Ownership denials (403/404/503) must reach the client, not be
        # flattened into a 200 "failed" payload by the catch-all below.
        raise
    except Exception as e:
        logger.exception(f"Error getting job details: {e}")
        return JobDetailResponse(
            success=False,
            error="Failed to get job details."
        )


@router.put("/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_job(job_id: str, request: Request):
    """
    Cancel a Job

    Owner-only: only the owner of the job's agent may cancel it. Sets the Job
    status to cancelled so it will no longer be polled for execution by
    JobTrigger.
    Only Jobs in pending or active status can be cancelled.
    Jobs in running status cannot be interrupted, but will be marked as cancelled and will not be re-executed.
    """
    logger.info(f"Cancel job request: {job_id}")

    try:
        db_client = await get_db_client()

        # Owner gate — deny non-owners before mutating anyone's job.
        if await _assert_job_owner(request, db_client, job_id) is None:
            return CancelJobResponse(
                success=False,
                error=f"Job not found: {job_id}"
            )

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

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error cancelling job: {e}")
        return CancelJobResponse(
            success=False,
            error="Failed to cancel job."
        )


@router.post("/complex", response_model=CreateJobComplexResponse)
async def create_job_complex(body: CreateJobComplexRequest, request: Request):
    """
    Batch create a group of Jobs with dependency relationships (Job Complex)

    Owner-only: the target agent must be owned by the caller, and every job is
    created under the caller's authenticated identity — never the client-supplied
    user_id (which the old signature trusted, an IDOR: any caller could create
    and run jobs under another user's agent).

    Workflow:
    1. Validate the request graph: task_keys unique (400), every depends_on
       entry names a job in the list (success=False)
    2. Topological sort to determine creation order (cycle -> 400)
    3. Batch create Jobs, mapping task_key to actual job_id
    4. Root Jobs (no dependencies) are created PENDING and fire immediately;
       dependent Jobs are created BLOCKED and are re-armed by the dependency
       chain once their prerequisites reach a terminal state (B-16)

    Dependency relationships are stored in the depends_on field within payload
    """
    logger.info(f"Creating Job Complex: {len(body.jobs)} jobs")

    await require_agent_owner(request, body.agent_id)
    # Identity is the authenticated caller, not the request body — the agent is
    # already proven to be theirs by assert_owned above.
    user_id = await current_user_id(request)

    try:
        # 1. Validate the request graph. A duplicate task_key is a 400 like a
        # cycle (review I9): the topological sort keys jobs by task_key, so a
        # repeated key would silently keep only the last job and report
        # success with one job_id fewer — the quiet failure mode is worse
        # than the pre-sort behaviour it replaced. Unknown dependencies keep
        # their historical `200 success=False` envelope (the frontend reads
        # `error` from that body); structural request errors are 400.
        task_keys: set[str] = set()
        duplicate_keys: set[str] = set()
        for job in body.jobs:
            if job.task_key in task_keys:
                duplicate_keys.add(job.task_key)
            task_keys.add(job.task_key)
        duplicates = sorted(duplicate_keys)
        if duplicates:
            raise HTTPException(
                status_code=400,
                detail=f"duplicate task_key(s) in job list: {', '.join(duplicates)}",
            )
        for job in body.jobs:
            for dep in job.depends_on:
                if dep not in task_keys:
                    return CreateJobComplexResponse(
                        success=False,
                        error=f"Invalid dependency: '{dep}' not found in job list"
                    )

        # 1.5. Topological sort (B-16): create dependencies before their
        # dependents regardless of request order, and reject a cycle loudly
        # (400, naming the task_keys involved) instead of leaving every job
        # in it BLOCKED forever with no diagnostic.
        ordered_jobs, cycle_error = _topological_sort_job_complex(body.jobs)
        if ordered_jobs is None:
            raise HTTPException(status_code=400, detail=cycle_error)

        # 2. Generate group_id
        group_id = body.group_id or f"group_{uuid4().hex[:8]}"

        # 3. Create Jobs via JobInstanceService (creates ModuleInstance + Job records)
        db_client = await get_db_client()
        from narranexus_plugins.job_module.job_service import JobInstanceService
        job_service = JobInstanceService(db_client)

        job_ids = []
        task_key_to_job_id = {}  # task_key -> job_id mapping

        for job in ordered_jobs:
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
                agent_id=body.agent_id,
                user_id=user_id,
                title=job.title,
                description=job.description or "",
                job_type="one_off",
                trigger_config=TriggerConfig.immediate().model_dump(mode="json"),
                payload=payload_str,
                dependencies=depends_on_job_ids if depends_on_job_ids else None,
                # Deterministic batch with caller-chosen titles: sibling jobs in
                # one group are often near-duplicates by title ("X part 1/2") —
                # the LLM-repeat similarity gate must not merge or block them.
                confirm_new=True,
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

    except HTTPException:
        # The cycle-detection 400 above must reach the caller as a 400, not
        # be downgraded into a 200 success=False by the generic handler below.
        raise
    except Exception as e:
        logger.exception(f"Error creating Job Complex: {e}")
        return CreateJobComplexResponse(
            success=False,
            error="Failed to create job."
        )


@router.put("/{job_id}", response_model=JobUpdateResponse)
async def update_job(job_id: str, request: Request, body: JobUpdateBody):
    """
    Update Job fields — mirrors the `job_update` MCP tool, sharing the
    `narranexus_plugins.job_module.update_job_from_args` implementation.
    Only passed fields change.
    """
    await require_agent_owner(request, body.agent_id)

    # The ~90-line build-updates logic (effective_type ordering, trigger_config
    # + compute_next_run, next_run_time, status validation) is the shared
    # update_job_from_args — the seam's DirectStore and the agent-scoped route
    # call the same function, so the zombie-bug ordering fix can't drift between
    # the browser API and the agent path (rule #8).
    try:
        db_client = await get_db_client()
    except Exception as e:  # noqa: BLE001 — update_job_from_args never raises
        logger.exception(f"Error in job update: {e}")
        return JobUpdateResponse(success=False, job_id=job_id, message=f"Error: {e}")
    # Forward the mutable fields by unpacking the shared JobUpdateFields set
    # (everything on the body except agent_id) — so a field added to
    # JobUpdateFields flows through here automatically instead of being silently
    # dropped on the browser path, finishing the "declare the field list once"
    # story the seam route already gets via **body.model_dump().
    result = await update_job_from_args(
        db_client, body.agent_id, job_id, **body.model_dump(exclude={"agent_id"}),
    )
    return JobUpdateResponse(**result)


@router.put("/{job_id}/pause", response_model=JobPauseResponse)
async def pause_job(job_id: str, request: Request, body: JobPauseBody):
    """
    Pause a Job — mirrors the `job_pause` MCP tool
    (narranexus_plugins.job_module._job_mcp_tools job_pause).

    Unconditional: sets status to PAUSED regardless of the current status (no
    precondition check). This differs from the dashboard route's
    `/api/dashboard/jobs/{id}/pause`, which goes through
    `job_recovery.pause_job` and only allows pausing from active/pending —
    that route exists for the human-facing dashboard; this one exists so a
    non-agent caller gets the exact same semantics the agent's own
    `job_pause` tool has.
    """
    await require_agent_owner(request, body.agent_id)

    try:
        db_client = await get_db_client()
        job_repo = JobRepository(db_client)

        job = await job_repo.get_job(job_id)
        if not job:
            return JobPauseResponse(success=False, job_id=job_id, message=f"Job {job_id} not found")
        if job.agent_id != body.agent_id:
            return JobPauseResponse(
                success=False, job_id=job_id, message=f"Job {job_id} not found"
            )

        updated_rows = await job_repo.pause_job(job_id)

        return JobPauseResponse(
            success=updated_rows > 0,
            job_id=job_id,
            status="paused",
            message="Job paused successfully" if updated_rows > 0 else "Failed to pause job",
        )

    except Exception as e:
        logger.exception(f"Error pausing job {job_id}: {e}")
        return JobPauseResponse(success=False, job_id=job_id, message="Failed to pause job.")


@router.get("/search/semantic", response_model=JobSemanticSearchResponse)
async def search_jobs_semantic(
    request: Request,
    agent_id: str = Query(..., description="Agent ID"),
    query: str = Query(..., min_length=1, max_length=512, description="Natural language search query"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(10, ge=1, le=100, description="Max number of results"),
):
    """
    Search jobs by relevance to a natural-language query — the frontend-facing
    twin of the `job_retrieval_semantic` MCP tool. Both this route and the
    MCP-tool/seam path now call the ONE shared implementation
    (`narranexus_plugins.job_module.search_jobs_semantic`), so the job read
    semantics can't drift between the browser API and the agent path.

    Despite the name, this is BM25 keyword ranking, not vector cosine similarity
    — vectors were retired from job search; the tool kept its name for
    LLM-facing continuity.
    """
    await require_agent_owner(request, agent_id)

    # Same user-scoping decision as list_jobs above: the caller's own identity
    # is the filter — an "optional user_id" query param let any client read
    # anyone's jobs, and these endpoints must not reintroduce what that fix
    # removed (jobs are per-user records under the agent). This is deliberately
    # STRICTER than the agent-path seam route (agents/jobs.py), which trusts the
    # agent to pass a user_id (it queries its OWN agent's jobs).
    user_id = await current_user_id(request)
    try:
        db_client = await get_db_client()
    except Exception as e:  # noqa: BLE001 — the shared helper never raises
        logger.exception(f"Error in semantic job search: {e}")
        return JobSemanticSearchResponse(success=False, error="Failed to search jobs.")
    result = await _shared_search_semantic(db_client, agent_id, query, user_id, status, limit)
    return JobSemanticSearchResponse(**result)


@router.get("/search/keywords", response_model=JobKeywordSearchResponse)
async def search_jobs_by_keywords(
    request: Request,
    agent_id: str = Query(..., description="Agent ID"),
    keywords: List[str] = Query(..., min_length=1, max_length=20, description="Keywords to search for (matches if ANY keyword found)"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(20, ge=1, le=100, description="Max number of results"),
):
    """
    Search jobs by keyword matching — the frontend-facing twin of the
    `job_retrieval_by_keywords` MCP tool. Shares the ONE implementation
    (`narranexus_plugins.job_module.search_jobs_by_keywords`) with the
    agent path — see search_jobs_semantic above for the user-scoping rationale.
    """
    await require_agent_owner(request, agent_id)

    # Caller's own identity is the filter (stricter than the agent-path seam
    # route — see search_jobs_semantic above).
    user_id = await current_user_id(request)
    try:
        db_client = await get_db_client()
    except Exception as e:  # noqa: BLE001 — the shared helper never raises
        logger.exception(f"Error in keyword job search: {e}")
        return JobKeywordSearchResponse(success=False, error="Failed to search jobs.")
    result = await _shared_search_keywords(db_client, agent_id, keywords, user_id, status, limit)
    return JobKeywordSearchResponse(**result)


# ---- plugin contribution (batch 3c.5): this router belongs to builtin.job and is
# mounted by backend.plugins_host from backend.routes (no longer included by
# backend/main.py), so its paths 404 together with the plugin when disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("jobs", lambda: RouterSpec(router, "/api/jobs", tags=('Jobs',))),)
