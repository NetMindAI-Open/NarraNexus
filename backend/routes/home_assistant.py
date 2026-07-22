"""
@file_name: home_assistant.py
@author: NetMind.AI
@date: 2026-07-14
@description: Backend routes for the Home Assistant binding (config panel).

Endpoints (session-authed):
  GET  /api/home-assistant/binding?agent_id=   → agent's binding (token MASKED)
  PUT  /api/home-assistant/binding             → save {agent_id, base_url, token, verify_tls}
  POST /api/home-assistant/test                → probe a form base_url+token (ha.ping)
  POST /api/home-assistant/verify              → ping the SAVED binding via resolve_client

The binding is per-agent — a user with multiple Home Assistant instances (home
vs. office) can bind different agents to different HAs. The Long-Lived Token is
never returned in full — GET masks it so the panel can show "configured" without
leaking the secret.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from backend.auth import resolve_current_user_id
from xyz_agent_context.repository import HomeAssistantBindingRepository
from xyz_agent_context.schema.home_assistant_schema import HAConfig
from xyz_agent_context.utils.db_factory import get_db_client

router = APIRouter()


class HABindingBody(BaseModel):
    agent_id: str
    base_url: str
    token: str
    verify_tls: bool = True


class HATestBody(BaseModel):
    base_url: str
    token: str
    verify_tls: bool = True


class HAVerifyBody(BaseModel):
    agent_id: str


def _mask(token: str) -> str:
    """Show only the last 4 chars of a token."""
    return f"••••{token[-4:]}" if token and len(token) > 4 else "••••"


async def _require_agent_owner(request: Request, db, agent_id: str) -> None:
    """Authorize: the caller must OWN this agent, not just be authenticated.

    `resolve_current_user_id` only answers "who are you". The agent_id is
    attacker-controlled input, so we must verify it belongs to the current user
    or a cross-tenant IDOR opens up (read/overwrite others' HA bindings, or make
    the backend ping a victim's home with their stored token). Mirrors
    `backend/routes/lark.py::_verify_agent_ownership`.

    Local mode (no JWT identity) does not enforce ownership.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return  # local mode
    agent = await db.get_one("agents", {"agent_id": agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found.")
    if agent.get("created_by") != user_id:
        raise HTTPException(status_code=403, detail="Permission denied: you do not own this agent.")


@router.get("/binding")
async def get_binding(request: Request, agent_id: str) -> dict:
    """Return the agent's HA binding with the token masked (or {bound: False})."""
    await resolve_current_user_id(request)
    db = await get_db_client()
    await _require_agent_owner(request, db, agent_id)
    row = await HomeAssistantBindingRepository(db).get_by_agent(agent_id)
    if not row or not row.config_json:
        return {"bound": False}
    try:
        cfg = HAConfig.model_validate_json(row.config_json)
    except Exception:  # noqa: BLE001
        return {"bound": False, "corrupted": True}
    return {"bound": True, "base_url": cfg.base_url, "verify_tls": cfg.verify_tls, "token_masked": _mask(cfg.token)}


@router.put("/binding")
async def put_binding(request: Request, body: HABindingBody) -> dict:
    """Save/replace the agent's HA binding (base_url + token + verify_tls)."""
    await resolve_current_user_id(request)
    db = await get_db_client()
    await _require_agent_owner(request, db, body.agent_id)
    cfg = HAConfig(base_url=body.base_url, token=body.token, verify_tls=body.verify_tls)
    ok = await HomeAssistantBindingRepository(db).upsert_config(body.agent_id, cfg.model_dump_json())
    if not ok:
        raise HTTPException(status_code=500, detail="failed to save binding")
    return {"ok": True}


@router.post("/test")
async def test_connection(request: Request, body: HATestBody) -> dict:
    """Probe a base_url+token: ping the HA API and count entities."""
    await resolve_current_user_id(request)
    # Import here to avoid pulling module code into route import time.
    from xyz_agent_context.module.home_assistant_module._home_assistant_impl.ha_client import HAClient, HAError

    try:
        client = HAClient(body.base_url, body.token, body.verify_tls)
        await client.ping()
        states = await client.list_states()
        return {"ok": True, "entity_count": len(states)}
    except HAError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — never 500 on a user-supplied URL
        logger.warning(f"HA test connection failed: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/verify")
async def verify_binding(request: Request, body: HAVerifyBody) -> dict:
    """Verify the SAVED binding by pinging HA through the exact path the agent uses.

    Resolves the agent's binding via `resolve_client` (same read path as the MCP
    tools), so a green result proves the agent can actually reach HA — not just
    that a URL+token typed into the form work. Returns {ok, entity_count} or
    {ok: False, error}; the stored token is never returned.
    """
    await resolve_current_user_id(request)
    db = await get_db_client()
    await _require_agent_owner(request, db, body.agent_id)
    # Import here to avoid pulling module code into route import time.
    from xyz_agent_context.module.home_assistant_module._home_assistant_impl.binding import resolve_client

    client, reason = await resolve_client(db, body.agent_id)
    if client is None:
        return {"ok": False, "error": reason}
    try:
        await client.ping()
        states = await client.list_states()
        return {"ok": True, "entity_count": len(states)}
    except Exception as e:  # noqa: BLE001 — surface as a message, never 500
        logger.warning(f"HA verify binding failed for {body.agent_id}: {e}")
        return {"ok": False, "error": str(e)}
