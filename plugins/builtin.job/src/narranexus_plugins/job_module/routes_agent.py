"""
@file_name: routes_agent.py
@author:
@date: 2026-08-10
@description: Job READ + WRITE endpoints for the MCP data-access seam
(agent-scoped, owner-gated).

Byte-parity Http twins of the JobModule tools — reads (job_retrieval_by_id /
_semantic / _by_keywords) and writes (job_update, job_create, job_pause,
job_cancel) — so the HttpStore path of AgentDataStore can serve them without db
credentials in the mcp container. Each endpoint returns the EXACT dict the
seam's DirectStore returns — both call the shared, dialect-safe
``narranexus_plugins.job_module`` helpers — so the Http and in-process
paths are byte-identical. job_create's owner LLM-context setup + similar-title
embedding check run backend-side here (create_job_from_args), which is where the
DB lives in cloud; the mcp container has no creds to load the owner's config.

Distinct from ``backend/routes/jobs.py`` (the frontend-facing job API under
``/jobs`` with response_model shapes): these live under
``/api/agents/{agent_id}/jobs`` and are owner-gated like the other seam routes.
Searches are POST (their args — keyword lists, filters — travel in the body);
by-id is a GET.

user_id scoping — DELIBERATELY different from ``backend/routes/jobs.py``:
these search endpoints accept an OPTIONAL ``user_id`` from the caller (default
None = all users' jobs under the agent), because their caller is the agent
itself (via the identity-forwarded seam) querying its OWN agent's jobs — and
this exactly preserves the MCP tool's signature (``user_id: Optional[str]``), so
DirectStore and HttpStore stay byte-identical. ``backend/routes/jobs.py`` makes
the OPPOSITE choice (user_id forced to the authenticated caller) on purpose:
that surface is a user's browser dashboard, where one user must not read another
user's jobs. Both are correct for their actor; ``assert_owned`` gates these to
the agent's owner, who already has this access through their own agent.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from narranexus.sdk.web import require_agent_owner
from narranexus_plugins.job_module import (
    fetch_job_by_id,
    search_jobs_semantic,
    search_jobs_by_keywords,
    update_job_from_args,
    create_job_from_args,
    pause_job_from_args,
    cancel_job_from_args,
)
from narranexus.platform.schema import JobUpdateFields
from narranexus.platform.utils.db.db_factory import get_db_client

router = APIRouter()


class JobUpdateSeamBody(JobUpdateFields):
    """Body for POST .../jobs/{job_id}/update — the job_update tool's mutable
    fields, inherited from the shared JobUpdateFields (declared once, same list
    as the frontend JobUpdateBody).

    ``extra="forbid"`` is load-bearing: this is the HttpStore write path, so a
    field added to update_job_from_args + the MCP tool but forgotten HERE must
    fail LOUDLY (422 → HttpStore._parse_dict surfaces "invalid arguments")
    rather than be silently dropped while DirectStore applies it — that silent
    local/cloud divergence is exactly what the seam's byte-parity exists to
    prevent. This is chosen "loud" over "surgical": the MCP tool's ``fields``
    literal always sends all nine keys, so on drift EVERY cloud job_update 422s,
    not just the call that set the new field — on-call should read a sudden
    "job_update always fails" as a missing field on THIS body, not a lost route.
    (The frontend JobUpdateBody deliberately keeps the default extra="ignore":
    it calls update_job_from_args in-process, so it has no silent-drop path to
    guard, and it adds a required field rather than forbidding unknown ones.)"""
    model_config = ConfigDict(extra="forbid")


class JobCreateSeamBody(BaseModel):
    """Body for POST .../jobs — the job_create tool's arguments (agent_id comes
    from the path). ``extra="forbid"`` is load-bearing for the same reason as
    JobUpdateSeamBody: this is the HttpStore write path, so a create field added
    to the MCP tool + create_job_from_args but forgotten HERE must 422 loudly
    (→ HttpStore surfaces "invalid arguments") rather than be silently dropped
    while DirectStore applies it. trigger_config stays a free dict here — its
    shape is validated downstream by create_job_with_instance (per job_type),
    exactly as the in-process tool path does."""
    model_config = ConfigDict(extra="forbid")

    user_id: str
    title: str
    description: str
    job_type: str
    trigger_config: dict
    payload: str
    notification_method: str = "direct"
    task_key: Optional[str] = None
    depends_on_job_ids: Optional[List[str]] = None
    related_entity_id: Optional[str] = None
    narrative_id: Optional[str] = None
    confirm_new: bool = False


class JobSemanticSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    user_id: Optional[str] = None
    status: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)


class JobKeywordSearchBody(BaseModel):
    keywords: List[str] = Field(min_length=1)
    user_id: Optional[str] = None
    status: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


@router.get("/{agent_id}/jobs/{job_id}")
async def job_by_id(agent_id: str, job_id: str, request: Request) -> dict:
    """Full detail of one job — twin of the ``job_retrieval_by_id`` MCP tool."""
    await require_agent_owner(request, agent_id)
    try:
        return await fetch_job_by_id(await get_db_client(), agent_id, job_id)
    except Exception as e:  # noqa: BLE001 — get_db_client() only; fetch never raises
        logger.warning(f"job_by_id failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/jobs/search-semantic")
async def job_search_semantic(agent_id: str, body: JobSemanticSearchBody, request: Request) -> dict:
    """Keyword (BM25) job search — twin of the ``job_retrieval_semantic`` tool."""
    await require_agent_owner(request, agent_id)
    try:
        return await search_jobs_semantic(
            await get_db_client(), agent_id, body.query, body.user_id, body.status, body.limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"job_search_semantic failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/jobs/search-keywords")
async def job_search_keywords(agent_id: str, body: JobKeywordSearchBody, request: Request) -> dict:
    """Keyword-list job search — twin of the ``job_retrieval_by_keywords`` tool."""
    await require_agent_owner(request, agent_id)
    try:
        return await search_jobs_by_keywords(
            await get_db_client(), agent_id, body.keywords, body.user_id, body.status, body.limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"job_search_keywords failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/jobs/{job_id}/update")
async def job_update(agent_id: str, job_id: str, body: JobUpdateSeamBody, request: Request) -> dict:
    """Update a job's fields — twin of the ``job_update`` MCP tool. Shares the
    ``update_job_from_args`` implementation with DirectStore (byte-parity) and
    the frontend ``/api/jobs/{job_id}`` route."""
    await require_agent_owner(request, agent_id)
    try:
        db = await get_db_client()
    except Exception as e:  # noqa: BLE001 — update_job_from_args never raises
        logger.warning(f"job_update failed: {e}")
        return {"success": False, "job_id": job_id, "message": f"Error: {e}"}
    return await update_job_from_args(db, agent_id, job_id, **body.model_dump())


@router.post("/{agent_id}/jobs")
async def job_create(agent_id: str, body: JobCreateSeamBody, request: Request) -> dict:
    """Create a job — twin of the ``job_create`` MCP tool. Shares the
    ``create_job_from_args`` implementation with DirectStore (byte-parity); the
    similar-title embedding check + owner LLM-context setup run HERE, backend
    side, which is where the DB (and thus the owner's LLM config) lives in
    cloud."""
    await require_agent_owner(request, agent_id)
    try:
        db = await get_db_client()
    except Exception as e:  # noqa: BLE001 — create_job_from_args never raises
        logger.warning(f"job_create failed: {e}")
        return {"success": False, "error": str(e)}
    return await create_job_from_args(db, agent_id, **body.model_dump())


@router.put("/{agent_id}/jobs/{job_id}/pause")
async def job_pause(agent_id: str, job_id: str, request: Request) -> dict:
    """Pause a job — twin of the ``job_pause`` MCP tool. Shares
    ``pause_job_from_args`` with DirectStore (byte-parity)."""
    await require_agent_owner(request, agent_id)
    try:
        db = await get_db_client()
    except Exception as e:  # noqa: BLE001 — pause_job_from_args never raises
        logger.warning(f"job_pause failed: {e}")
        return {"success": False, "job_id": job_id, "message": f"Error: {e}"}
    return await pause_job_from_args(db, agent_id, job_id)


@router.put("/{agent_id}/jobs/{job_id}/cancel")
async def job_cancel(agent_id: str, job_id: str, request: Request) -> dict:
    """Cancel a job (terminal) — twin of the ``job_cancel`` MCP tool. Shares
    ``cancel_job_from_args`` with DirectStore (byte-parity)."""
    await require_agent_owner(request, agent_id)
    try:
        db = await get_db_client()
    except Exception as e:  # noqa: BLE001 — cancel_job_from_args never raises
        logger.warning(f"job_cancel failed: {e}")
        return {"success": False, "job_id": job_id, "message": f"Error: {e}"}
    return await cancel_job_from_args(db, agent_id, job_id)


# ---- plugin contribution (batch 3c.4): this twin router of the data-access
# seam is provided by builtin.job through backend.routes (mounted under /api/agents
# by backend.plugins_host, no longer included by routes/agents/core.py), so the
# HTTP twin disappears together with the capability when the plugin is disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("agents_jobs", lambda: RouterSpec(router, "/api/agents", tags=("Agents",))),)
