"""
@file_name: narramessenger.py
@date: 2026-06-18
@description: Backend API routes for NarraMessenger binding + status.

Endpoints:
  GET    /api/narramessenger/credential  — sanitised binding info for an agent
  POST   /api/narramessenger/bind        — bind from a pasted bind command/link
  POST   /api/narramessenger/unbind      — remove the binding

Bind logic lives in ``_narramessenger_service.do_bind`` (shared with the
``narra_bind`` MCP tool) — these routes are the frontend "paste the bind link"
entry point.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from loguru import logger

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredentialManager,
)
from xyz_agent_context.module.narramessenger_module._narramessenger_service import (
    do_bind,
    do_unbind,
)

router = APIRouter()

_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"


class AgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)


class BindRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)
    bind_command: str = Field(min_length=1, max_length=2048)


async def _get_db():
    from xyz_agent_context.utils.db_factory import get_db_client
    return await get_db_client()


async def _verify_agent_ownership(request: Request, agent_id: str) -> str | None:
    """Local mode: no enforcement. Cloud mode: agent.created_by must match JWT."""
    if not hasattr(request.state, "user_id") or not request.state.user_id:
        return None
    user_id = request.state.user_id
    db = await _get_db()
    agent = await db.get_one("agents", {"agent_id": agent_id})
    if not agent:
        return f"Agent {agent_id} not found."
    if agent.get("created_by") != user_id:
        return "Permission denied: you do not own this agent."
    return None


@router.get("/credential")
async def get_credential(request: Request, agent_id: str) -> dict[str, Any]:
    """Sanitised binding info (no bearer token). ``data`` is None if unbound."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    mgr = NarramessengerCredentialManager(db)
    data = await mgr.get_public(agent_id)
    return {"success": True, "data": data}


@router.post("/bind")
async def bind(request: Request, body: BindRequest) -> dict[str, Any]:
    """Bind from a pasted bind command/link (drives the Gateway bind)."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    result = await do_bind(db, body.agent_id, body.bind_command)
    if result.get("success"):
        logger.info(f"NarraMessenger bound: agent={body.agent_id}")
    return result


@router.post("/unbind")
async def unbind(request: Request, body: AgentRequest) -> dict[str, Any]:
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    return await do_unbind(db, body.agent_id)
