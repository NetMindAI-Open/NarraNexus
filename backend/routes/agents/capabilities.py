"""
@file_name: capabilities.py
@author: Bin Liang
@date: 2026-09-04
@description: Per-agent capability enablement (plugin platform batch 5c).

Endpoints (mounted under /api/agents by agents/core.py):
  GET    /{agent_id}/capabilities                  — every registered module with its state + the context budget
  PUT    /{agent_id}/capabilities/{module_class}   — {"enabled": bool}; base modules cannot be disabled
  DELETE /{agent_id}/capabilities/{module_class}   — back to the default rule (builtin on, plugin off)

Auth: the caller must OWN the agent (agents.created_by) — the same gate as
llm_config. A change applies on the agent's next run (the loader reads the
table per turn).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from narranexus.platform.module_system.capability_service import CapabilityService
from backend.routes._ownership import assert_owned
from narranexus.platform.utils.db.db_factory import get_db_client

router = APIRouter()

class EnabledBody(BaseModel):
    enabled: bool = Field(...)


async def _require_owner(agent_id: str, request: Request):
    """The one ownership check every agent route uses (backend/routes/_ownership.assert_owned) — no eleventh copy."""
    await assert_owned(request, agent_id)
    return await get_db_client()


@router.get("/{agent_id}/capabilities")
async def get_capabilities(agent_id: str, request: Request) -> dict[str, Any]:
    db = await _require_owner(agent_id, request)
    return {"success": True, "data": await CapabilityService(db).overview(agent_id)}


@router.put("/{agent_id}/capabilities/{module_class}")
async def set_capability(agent_id: str, module_class: str, body: EnabledBody, request: Request) -> dict[str, Any]:
    db = await _require_owner(agent_id, request)
    try:
        result = await CapabilityService(db).set_enabled(agent_id, module_class, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"success": True, "data": result}


@router.delete("/{agent_id}/capabilities/{module_class}")
async def reset_capability(agent_id: str, module_class: str, request: Request) -> dict[str, Any]:
    db = await _require_owner(agent_id, request)
    removed = await CapabilityService(db).reset(agent_id, module_class)
    return {"success": True, "data": {"module_class": module_class, "reset": removed}}


__all__ = ["router"]
