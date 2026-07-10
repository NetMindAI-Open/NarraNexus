"""
@file_name: agents_llm_config.py
@author: rujing.yan
@date: 2026-07-09
@description: Per-agent LLM config overrides (agent framework + model + helper).

An agent inherits its owner's user-level slots by default; these endpoints
read/write the optional per-agent override rows (``agent_slots``) so a single
agent can pin its own coding-agent framework + model (agent slot) and its own
helper model (helper_llm slot), independent of the owner default.

Endpoints (mounted under /api/agents):
  GET    /{agent_id}/llm-config              — per-slot inheriting/effective/override
  PUT    /{agent_id}/llm-config/{slot_name}  — set an override
  DELETE /{agent_id}/llm-config/{slot_name}  — reset a slot to inherit ("all" = both)

Auth: the caller must OWN the agent (agents.created_by). In cloud mode a
non-staff caller may not bind an OAuth-source provider (it would ride the
shared CLI credentials) — mirrors the framework-switch gate in providers.py.

No hot-reload: config is resolved per run from the DB, so a change here takes
effect on the agent's NEXT run (set_user_config is ContextVar/task-scoped and
cannot reach an already-running loop).
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from backend.auth import resolve_current_user_id
from xyz_agent_context.agent_framework.agent_slot_service import AgentSlotService
from xyz_agent_context.schema.provider_schema import SlotName
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.deployment_mode import is_cloud_mode

router = APIRouter()

_OAUTH_SOURCES = frozenset({"claude_oauth", "codex_oauth"})


def _is_staff(request: Request) -> bool:
    return getattr(request.state, "role", "") == "staff"


async def _require_owner(agent_id: str, request: Request) -> tuple[str, dict]:
    """Return (user_id, agent_row) after asserting the caller owns the agent.

    Raises 401 (no identity), 404 (agent missing), or 403 (not owner).
    """
    user_id = await resolve_current_user_id(request)
    db = await get_db_client()
    agent_row = await db.get_one("agents", {"agent_id": agent_id})
    if not agent_row:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found.")
    if agent_row.get("created_by") != user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the agent's owner can change its LLM config.",
        )
    return user_id, agent_row


def _parse_params(raw) -> tuple[str, str]:
    if not raw:
        return "", ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("thinking") or ""), str(data.get("reasoning_effort") or "")


def _slot_view(slot_name: str, row: Optional[dict]) -> Optional[dict]:
    """Render a slot row (override OR owner default) into a flat dict, or None
    when the row is missing/unbound."""
    if not row or not row.get("provider_id"):
        return None
    thinking, reasoning_effort = _parse_params(row.get("params_json"))
    view = {
        "provider_id": row.get("provider_id"),
        "model": row.get("model") or "",
        "thinking": thinking,
        "reasoning_effort": reasoning_effort,
    }
    if slot_name == SlotName.AGENT.value:
        view["agent_framework"] = row.get("agent_framework") or "claude_code"
    return view


class SetAgentSlotRequest(BaseModel):
    provider_id: str
    model: str
    thinking: str = ""
    reasoning_effort: str = ""
    # Only meaningful for the agent slot; ignored for helper_llm.
    agent_framework: Optional[str] = None


@router.get("/{agent_id}/llm-config")
async def get_agent_llm_config(agent_id: str, request: Request):
    """Per-slot view: is the agent inheriting the owner default, what is the
    effective config, and (if any) the raw override + owner default."""
    user_id, _ = await _require_owner(agent_id, request)
    db = await get_db_client()

    overrides = await AgentSlotService(db).get_agent_slots(agent_id)
    # Read the RAW user_slots rows, NOT UserProviderService.get_user_config():
    # that returns SlotConfig objects, which carry only provider_id / model /
    # thinking / reasoning_effort — they DROP the ``params_json`` and
    # ``agent_framework`` columns ``_slot_view`` reads. Feeding model_dump()
    # would make every owner-default framework read as claude_code and every
    # reasoning param read as auto, breaking inheritance for codex_cli owners.
    owner_rows = await db.get("user_slots", {"user_id": user_id})
    owner_by_slot = {r.get("slot_name"): r for r in owner_rows or []}

    slots_out: dict[str, dict] = {}
    for slot_name in (SlotName.AGENT.value, SlotName.HELPER_LLM.value):
        override_row = overrides.get(slot_name)
        owner_default_row = owner_by_slot.get(slot_name)
        override_view = _slot_view(slot_name, override_row)
        owner_view = _slot_view(slot_name, owner_default_row)
        slots_out[slot_name] = {
            "inheriting": override_view is None,
            "effective": override_view or owner_view,
            "override": override_view,
            "owner_default": owner_view,
        }

    return {"success": True, "data": {"agent_id": agent_id, "slots": slots_out}}


@router.put("/{agent_id}/llm-config/{slot_name}")
async def set_agent_llm_config(
    agent_id: str, slot_name: str, req: SetAgentSlotRequest, request: Request
):
    """Set a per-agent override for ``slot_name`` (agent | helper_llm)."""
    owner_id, _ = await _require_owner(agent_id, request)
    db = await get_db_client()

    # Cloud staff-gate: a non-staff caller may not bind an OAuth-source
    # provider to a per-agent slot — it would ride the shared CLI credentials
    # (same rule the framework switch enforces in providers.py). Scope the
    # lookup to the OWNER so we never inspect another user's provider row.
    if is_cloud_mode() and not _is_staff(request):
        prov = await db.get_one(
            "user_providers", {"user_id": owner_id, "provider_id": req.provider_id}
        )
        if prov is not None and prov.get("source") in _OAUTH_SOURCES:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Binding a CLI sign-in (OAuth) provider is staff-only in "
                    "cloud mode. Use one of your own API-key providers."
                ),
            )

    try:
        row = await AgentSlotService(db).set_agent_slot(
            agent_id,
            slot_name,
            req.provider_id,
            req.model,
            thinking=req.thinking,
            reasoning_effort=req.reasoning_effort,
            agent_framework=req.agent_framework,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        f"[agents_llm_config] set override agent={agent_id} slot={slot_name} "
        f"provider={req.provider_id} model={req.model!r} "
        f"framework={req.agent_framework!r} (applies next run)"
    )
    return {
        "success": True,
        "data": {"slot": _slot_view(slot_name, row)},
    }


@router.delete("/{agent_id}/llm-config/{slot_name}")
async def reset_agent_llm_config(agent_id: str, slot_name: str, request: Request):
    """Reset a slot to inherit the owner default. ``slot_name='all'`` clears
    both slots."""
    await _require_owner(agent_id, request)
    db = await get_db_client()
    target = None if slot_name == "all" else slot_name
    if target is not None and target not in [s.value for s in SlotName]:
        raise HTTPException(status_code=400, detail=f"Invalid slot: {slot_name}")
    await AgentSlotService(db).clear_agent_slot(agent_id, target)
    logger.info(
        f"[agents_llm_config] reset override agent={agent_id} "
        f"slot={slot_name} (inherits owner default next run)"
    )
    return {"success": True}
