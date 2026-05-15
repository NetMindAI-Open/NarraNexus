"""
@file_name: telegram.py
@date: 2026-05-09
@description: Backend API routes for Telegram bot binding + management.

Endpoints:
  POST   /api/telegram/bind         — Bind a Telegram bot to an agent
  GET    /api/telegram/credential   — Get sanitized credential view (NO token)
  POST   /api/telegram/test         — Re-validate stored token via getMe
  POST   /api/telegram/unbind       — Remove the binding
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
    TelegramCredentialManager,
)
from xyz_agent_context.module.telegram_module._telegram_service import (
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
    # Optional: owner's Telegram @username (with or without leading @).
    # Backend resolves to numeric user_id at bind time → enables the
    # is_owner_interacting trust signal.
    owner_username: str = Field(default="", max_length=64)


class AgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)


# =========================================================================
# Helpers
# =========================================================================


async def _get_db():
    from xyz_agent_context.utils.db_factory import get_db_client

    return await get_db_client()


async def _verify_agent_ownership(request: Request, agent_id: str) -> str | None:
    # Local-mode caveat: in environments without the auth middleware,
    # request.state.user_id is unset and EVERY route below is effectively
    # unauthenticated. Mirror of slack.py's _verify_agent_ownership — see
    # that docstring for the full posture note.
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
async def bind_telegram_bot(request: Request, body: BindRequest) -> dict[str, Any]:
    """Bind a Telegram bot to an agent.

    Validates the token via ``getMe`` and (defensively) calls
    ``deleteWebhook`` so subsequent long-poll won't 409. Optionally
    resolves the owner's @username to a numeric user_id.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = TelegramCredentialManager(db)
    result = await do_bind(
        mgr,
        body.agent_id,
        body.bot_token,
        owner_username=body.owner_username,
    )
    if result.get("success"):
        data = result.get("data") or {}
        bot = data.get("bot_username", "?")
        owner = data.get("owner_name") or "(no owner)"
        logger.info(
            f"Telegram bot bound: agent={body.agent_id} bot=@{bot} owner={owner}"
        )
    return result


@router.get("/credential")
async def get_telegram_credential(request: Request, agent_id: str) -> dict[str, Any]:
    """Return the sanitised credential view (NO raw token) for an agent."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = TelegramCredentialManager(db)
    pub = await mgr.get_public(agent_id)
    return {"success": True, "data": pub}


@router.post("/test")
async def test_telegram_connection(
    request: Request, body: AgentRequest
) -> dict[str, Any]:
    """Re-run getMe against the stored credential (catches token revocation)."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = TelegramCredentialManager(db)
    return await do_test_connection(mgr, body.agent_id)


@router.post("/unbind")
async def unbind_telegram_bot(
    request: Request, body: AgentRequest
) -> dict[str, Any]:
    """Remove the Telegram credential row for an agent.

    POST not DELETE: some proxies strip request bodies from DELETE. See
    ``backend/routes/slack.py:unbind_slack_bot`` for the full rationale.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = TelegramCredentialManager(db)
    removed = await mgr.unbind(body.agent_id)
    if not removed:
        return {"success": False, "error": "no Telegram credential bound for this agent"}
    logger.info(f"Telegram bot unbound: agent={body.agent_id}")
    return {"success": True, "data": {"unbound": True}}
