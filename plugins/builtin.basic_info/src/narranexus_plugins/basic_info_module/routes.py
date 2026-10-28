"""
@file_name: routes.py
@author:
@date: 2026-08-10
@description: Narrative endpoints for the MCP data-access seam (PR-2).

Backend counterparts of the BasicInfoModule narrative MCP tools
(view_narrative / switch_narrative / create_narrative,
_basic_info_mcp_tools.py) so the Http path of AgentDataStore can serve them
without db credentials in the mcp container.

- GET  /{agent_id}/narratives/{narrative_id}        -> view_narrative
- GET  /{agent_id}/events/{event_id}                 -> view_event
- POST /{agent_id}/narratives/{narrative_id}/switch -> switch_narrative
- POST /{agent_id}/narratives                       -> create_narrative

The read endpoints (view_narrative / view_event / switch) return the EXACT dict
the seam's DirectStore returns — both call the shared, dialect-safe
``narranexus_plugins.basic_info_module._narrative_reads`` helpers — so the
Http and in-process paths are byte-identical (the raw-SQL MCP tools they replace
are migrated onto this seam).

Every endpoint calls ``assert_owned`` first (403 non-owner / 404 unknown
agent / no-op in local mode), matching the other MCP data-access-seam
routes (awareness.py, general_memory.py).

``create_narrative`` differs from its MCP-tool namesake on purpose: the MCP
tool is SIGNAL-ONLY (the agent_runtime hook does the actual create — see
``step_4_persist_results._detect_narrative_routing_signal``), because the
tool call and the runtime run in different processes and can't share state
any other way. This HTTP endpoint has no such constraint and no runtime
hook to defer to, so it creates the row directly via
``NarrativeService.create_narrative`` and returns a real ``narrative_id``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from narranexus.sdk.web import current_user_id
from narranexus.sdk.web import require_agent_owner
from narranexus.platform.narrative import NarrativeService
from narranexus_plugins.basic_info_module import (
    fetch_narrative_view,
    fetch_event_view,
    check_narrative_switch,
)
from narranexus.platform.utils.db.db_factory import get_db_client

router = APIRouter()


class CreateNarrativeRequest(BaseModel):
    # No user_id field: the narrative's owner is the AUTHENTICATED caller
    # (current_user_id), never a body-supplied id — assert_owned only
    # proves agent ownership, and a trusted-body user_id would let an owner
    # attribute rows to arbitrary users (pre-open review #6).
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)


class NarrativeCreateResponse(BaseModel):
    success: bool
    narrative_id: Optional[str] = None
    title: Optional[str] = None
    error: Optional[str] = None


@router.get("/{agent_id}/narratives/{narrative_id}")
async def view_narrative(agent_id: str, narrative_id: str, request: Request) -> dict:
    """Full info on one narrative (thread) including its chat history — the Http
    counterpart of the ``view_narrative`` MCP tool. Returns the SAME dict the
    seam's DirectStore returns (both call the shared ``fetch_narrative_view``),
    so the two paths are byte-identical by construction."""
    await require_agent_owner(request, agent_id)
    try:
        return await fetch_narrative_view(await get_db_client(), agent_id, narrative_id)
    except Exception as e:  # noqa: BLE001 — get_db_client() only; fetch_* never raises
        logger.warning(f"view_narrative failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/{agent_id}/events/{event_id}")
async def view_event(agent_id: str, event_id: str, request: Request) -> dict:
    """One past turn's full detail by event id — the Http counterpart of the
    ``view_event`` MCP tool (shared ``fetch_event_view``)."""
    await require_agent_owner(request, agent_id)
    try:
        return await fetch_event_view(await get_db_client(), agent_id, event_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"view_event failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/narratives/{narrative_id}/switch")
async def switch_narrative(agent_id: str, narrative_id: str, request: Request) -> dict:
    """Validate that ``narrative_id`` exists and belongs to ``agent_id`` — the
    Http counterpart of the ``switch_narrative`` MCP tool (shared
    ``check_narrative_switch``). A validation, not a re-attribution: turn
    re-filing only happens inside a live agent run (step_4_persist_results),
    which neither this route nor the tool can reach."""
    await require_agent_owner(request, agent_id)
    try:
        return await check_narrative_switch(await get_db_client(), agent_id, narrative_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"switch_narrative failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/narratives", response_model=NarrativeCreateResponse)
async def create_narrative(agent_id: str, body: CreateNarrativeRequest, request: Request):
    """Create a new narrative (thread) for ``agent_id`` and return its id.

    See the module docstring for why this actually creates, unlike the
    signal-only ``create_narrative`` MCP tool.
    """
    await require_agent_owner(request, agent_id)
    if not (body.title or "").strip():
        return NarrativeCreateResponse(success=False, error="title is required")
    user_id = await current_user_id(request)
    try:
        db = await get_db_client()
        service = NarrativeService(agent_id=agent_id, database_client=db)
        narrative = await service.create_narrative(
            agent_id=agent_id,
            user_id=user_id,
            title=body.title,
            description=body.description,
        )
        logger.info(f"[NarrativeRoute] create_narrative -> {narrative.id} (agent={agent_id})")
        return NarrativeCreateResponse(success=True, narrative_id=narrative.id, title=body.title)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"create_narrative failed: {e}")
        return NarrativeCreateResponse(success=False, error=str(e))


# ---- plugin contribution (batch 3c.4): this twin router of the data-access
# seam is provided by builtin.basic_info through backend.routes (mounted under /api/agents
# by backend.plugins_host, no longer included by routes/agents/core.py), so the
# HTTP twin disappears together with the capability when the plugin is disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("agents_narrative", lambda: RouterSpec(router, "/api/agents", tags=("Agents",))),)
