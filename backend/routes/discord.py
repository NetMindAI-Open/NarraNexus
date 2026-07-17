"""
@file_name: discord.py
@date: 2026-06-16
@description: Backend API routes for Discord bot binding + management.

Endpoints:
  POST   /api/discord/bind         — Bind a Discord bot to an agent
  GET    /api/discord/credential   — Get sanitized credential view (NO token)
  POST   /api/discord/test         — Re-validate stored token via GET /users/@me
  POST   /api/discord/unbind       — Remove the binding

Mirrors backend/routes/telegram.py. Auth posture: in local mode (no JWT
middleware) request.state.user_id is unset and every route is effectively
unauthenticated; in cloud mode the agent's ``created_by`` must match the
caller. See slack.py's _verify_agent_ownership docstring for the full note.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.module.discord_module._discord_credential_manager import (
    DiscordCredentialManager,
)
from xyz_agent_context.module.discord_module._discord_service import (
    do_bind,
    do_test_connection,
)


router = APIRouter()

# Pattern for safe agent_id values (alphanumeric + underscore + hyphen)
_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"


# =========================================================================
# Request schemas
# =========================================================================


class BindRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)
    bot_token: str = Field(min_length=10, max_length=512)
    # Optional: owner's numeric Discord user id. Enables the
    # is_owner_interacting trust signal.
    owner_user_id: str = Field(default="", max_length=64)


class AgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)


class SetActiveRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)
    active: bool


# =========================================================================
# Helpers
# =========================================================================


async def _get_db():
    from xyz_agent_context.utils.db_factory import get_db_client

    return await get_db_client()


async def _verify_agent_ownership(request: Request, agent_id: str) -> str | None:
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


# =========================================================================
# Endpoints
# =========================================================================


@router.post("/bind")
async def bind_discord_bot(request: Request, body: BindRequest) -> dict[str, Any]:
    """Bind a Discord bot to an agent.

    Validates the token via ``GET /users/@me`` and optionally resolves the
    owner's display name from a supplied numeric Discord user id.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = DiscordCredentialManager(db)
    result = await do_bind(
        mgr, body.agent_id, body.bot_token, owner_user_id=body.owner_user_id
    )
    if result.get("success"):
        data = result.get("data") or {}
        bot = data.get("bot_username", "?")
        owner = data.get("owner_name") or "(no owner)"
        logger.info(
            f"Discord bot bound: agent={body.agent_id} bot=@{bot} owner={owner}"
        )
    return result


@router.get("/credential")
async def get_discord_credential(request: Request, agent_id: str) -> dict[str, Any]:
    """Return the sanitised credential view (NO raw token) for an agent."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = DiscordCredentialManager(db)
    pub = await mgr.get_public(agent_id)
    return {"success": True, "data": pub}


@router.post("/test")
async def test_discord_connection(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Re-run GET /users/@me against the stored credential (catches token reset)."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = DiscordCredentialManager(db)
    return await do_test_connection(mgr, body.agent_id)


@router.post("/unbind")
async def unbind_discord_bot(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Remove the Discord credential row for an agent.

    POST not DELETE: some proxies strip request bodies from DELETE. See
    ``backend/routes/slack.py:unbind_slack_bot`` for the full rationale.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = DiscordCredentialManager(db)
    removed = await mgr.unbind(body.agent_id)
    if not removed:
        return {"success": False, "error": "no Discord credential bound for this agent"}
    logger.info(f"Discord bot unbound: agent={body.agent_id}")
    return {"success": True, "data": {"unbound": True}}


@router.post("/set-active")
async def set_discord_active(request: Request, body: SetActiveRequest) -> dict[str, Any]:
    """Activate/deactivate the Discord credential (flip ``enabled``) without a
    re-bind. Primary use: activating a credential imported (inactive) from a
    bundle. The trigger's credential watcher picks up the change on its next
    poll and claims the single connection slot for this bot — so only activate
    here once the source environment is no longer connected to the same bot.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    mgr = DiscordCredentialManager(db)
    ok = await mgr.set_enabled(body.agent_id, body.active)
    if not ok:
        return {"success": False, "error": "No Discord bot bound to this agent."}
    logger.info(
        f"Discord credential {'activated' if body.active else 'deactivated'}: "
        f"agent={body.agent_id}"
    )
    return {"success": True, "enabled": body.active}
