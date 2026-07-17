"""
@file_name: slack.py
@date: 2026-05-08
@description: Backend API routes for Slack bot binding + management.

Endpoints:
  POST   /api/slack/bind         — Bind a Slack workspace to an agent
  GET    /api/slack/credential   — Get sanitized credential view (NO tokens)
  POST   /api/slack/test         — Re-validate stored tokens via auth.test
  POST   /api/slack/unbind       — Remove the binding
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.module.slack_module._slack_credential_manager import (
    SlackCredentialManager,
)
from xyz_agent_context.module.slack_module._slack_service import (
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
    app_token: str = Field(min_length=10, max_length=512)
    # Optional: owner's Slack email. When provided, backend resolves
    # owner_user_id via users.lookupByEmail at bind time → enables the
    # is_owner_interacting trust signal.
    owner_email: str = Field(default="", max_length=254)


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
    """Returns error message string when caller doesn't own the agent.

    Local mode (no JWT) skips enforcement; cloud mode requires the
    agent's ``created_by`` match the JWT user_id.

    Security posture note: when this process runs without the auth
    middleware that populates ``request.state.user_id`` (the local-mode
    case), every Slack route is effectively unauthenticated — any HTTP
    caller that can reach the backend port can bind / unbind / test any
    bot. This is the intentional trade-off for developer ergonomics:
    no real Docker bind exposes 8000 to the network by default. Do NOT
    add sensitive operations behind this helper assuming auth — they
    won't have any in local mode. The Telegram, Lark, and (future)
    other IM-channel routes mirror this exact contract; keep them in
    lockstep when changing.
    """
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
async def bind_slack_bot(request: Request, body: BindRequest) -> dict[str, Any]:
    """Bind a Slack workspace to an agent.

    Validates tokens via ``auth.test`` and back-fills team / bot identity
    on the credential row.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    # Light validation — pydantic enforces shape, we just guard "@".
    if body.owner_email and "@" not in body.owner_email:
        return {"success": False, "error": "Invalid email format for owner_email."}

    db = await _get_db()
    mgr = SlackCredentialManager(db)
    result = await do_bind(
        mgr,
        body.agent_id,
        body.bot_token,
        body.app_token,
        owner_email=body.owner_email,
    )
    if result.get("success"):
        data = result.get("data") or {}
        team = data.get("team_name", "?")
        owner = data.get("owner_name") or "(no owner)"
        logger.info(f"Slack bot bound: agent={body.agent_id} team={team} owner={owner}")
    return result


@router.get("/credential")
async def get_slack_credential(request: Request, agent_id: str) -> dict[str, Any]:
    """Return the sanitized credential view (NO raw tokens) for an agent."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = SlackCredentialManager(db)
    pub = await mgr.get_public(agent_id)
    return {"success": True, "data": pub}


@router.post("/test")
async def test_slack_connection(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Re-run ``auth.test`` against the stored credential (catches token revocation)."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = SlackCredentialManager(db)
    return await do_test_connection(mgr, body.agent_id)


@router.post("/unbind")
async def unbind_slack_bot(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Remove the Slack credential row for an agent.

    POST not DELETE: some proxies (Nginx default, AWS ALB) strip request
    bodies from DELETE, which would make ``agent_id`` arrive as ``None``
    and produce a generic 422. POST is well-behaved across infra.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = SlackCredentialManager(db)
    removed = await mgr.unbind(body.agent_id)
    if not removed:
        return {"success": False, "error": "no Slack credential bound for this agent"}
    logger.info(f"Slack bot unbound: agent={body.agent_id}")
    return {"success": True, "data": {"unbound": True}}


@router.post("/set-active")
async def set_slack_active(request: Request, body: SetActiveRequest) -> dict[str, Any]:
    """Activate/deactivate the Slack credential (flip ``enabled``) without a
    re-bind. Primary use: activating a credential imported (inactive) from a
    bundle. The trigger's credential watcher picks up the change on its next
    poll and claims the single connection slot for this bot — so only activate
    here once the source environment is no longer connected to the same bot.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    mgr = SlackCredentialManager(db)
    ok = await mgr.set_enabled(body.agent_id, body.active)
    if not ok:
        return {"success": False, "error": "No Slack bot bound to this agent."}
    logger.info(
        f"Slack credential {'activated' if body.active else 'deactivated'}: "
        f"agent={body.agent_id}"
    )
    return {"success": True, "enabled": body.active}
