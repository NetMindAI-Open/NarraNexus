"""
@file_name: agents_circuit_breaker.py
@author:
@date: 2026-07-13
@description: Inspect + manually reset an agent's real-time circuit-breaker.

Endpoints:
  GET  /{agent_id}/circuit-breaker         — current breaker status
  POST /{agent_id}/circuit-breaker/reset   — manually re-enable (clear pause)

The real-time-layer circuit-breaker (see
``agent_framework/agent_circuit_breaker.py``) auto-pauses an agent whose turns
keep failing for auth/quota reasons, and auto-resumes it when the owner
reconfigures the provider. This route is the MANUAL recovery half — the
owner's "Resume" button — for when they want to re-enable it directly.

Per-viewer tenancy (same pattern as agents_bus_failures.py): viewer_id from
the session only. A breaker may only be read/reset by the OWNER of the target
agent (agents.created_by == viewer_id); otherwise 404 (masks "no such agent"
and "not yours" identically).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from xyz_agent_context.agent_framework.agent_circuit_breaker import reset_agent
from xyz_agent_context.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from xyz_agent_context.utils.db_factory import get_db_client

router = APIRouter()


async def _resolve_viewer_id(request: Request) -> str:
    if "user_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="user_id query param not accepted; viewer identified by session",
        )
    return await resolve_current_user_id(request)


async def _require_owned_agent(db, agent_id: str, viewer_id: str) -> None:
    # Use the dialect-safe helper (matching agent_circuit_breaker._resolve_owner)
    # rather than hand-rolled SQL — 404 masks "no such agent" and "not yours".
    row = await db.get_one("agents", {"agent_id": agent_id})
    if not row or row.get("created_by") != viewer_id:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/circuit-breaker")
async def get_circuit_breaker(request: Request, agent_id: str):
    """Return the agent's circuit-breaker status (or a synthetic ACTIVE state
    when it has never failed and thus has no row yet)."""
    viewer_id = await _resolve_viewer_id(request)
    db = await get_db_client()
    await _require_owned_agent(db, agent_id, viewer_id)

    row = await AgentCircuitBreakerRepository(db).get(agent_id)
    if row is None:
        return {
            "success": True,
            "agent_id": agent_id,
            "cb_status": "active",
            "paused_reason": None,
            "consecutive_failure_count": 0,
            "cooldown_until": None,
            "last_error": None,
        }
    return {
        "success": True,
        "agent_id": agent_id,
        "cb_status": row.cb_status,
        "paused_reason": row.paused_reason,
        "consecutive_failure_count": row.consecutive_failure_count,
        "cooldown_until": row.cooldown_until.isoformat() if row.cooldown_until else None,
        "last_error": row.last_error,
    }


@router.post("/{agent_id}/circuit-breaker/reset")
async def reset_circuit_breaker(request: Request, agent_id: str):
    """Manually clear the agent's breaker back to ACTIVE so its next turn runs.

    Idempotent — resetting an already-active agent is a no-op.
    """
    viewer_id = await _resolve_viewer_id(request)
    db = await get_db_client()
    await _require_owned_agent(db, agent_id, viewer_id)

    await reset_agent(agent_id, db=db)
    logger.info(
        f"[agents_circuit_breaker] viewer {viewer_id} manually reset "
        f"circuit-breaker for agent {agent_id}"
    )
    return {"success": True, "agent_id": agent_id, "cb_status": "active"}
