"""
@file_name: arena.py
@author: Bin Liang
@date: 2026-06-15
@description: NetMind Agent Arena onboarding endpoint. A user who lands from
              Arena (already authenticated via NetMind Power SSO) calls
              POST /api/arena/provision to be handed a ready-to-play Arena
              agent. Idempotent — one provisioned Arena agent per user.

Auth-required (not in AUTH_EXEMPT_PATHS): the caller must already hold a session
JWT, which the existing inbound-token / netmind-login SSO flow mints on landing.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from xyz_agent_context.utils.db_factory import get_db_client

router = APIRouter(prefix="/api/arena", tags=["Arena"])


@router.post("/provision")
async def provision_arena(request: Request) -> dict:
    """
    Ensure the authenticated user has a provisioned Arena agent and return it.

    Returns `{success, reused, status, agent_id, arena_agent_id, arena_name,
    owner_bind, timings_ms, ...}`. Safe to call on every Arena landing — the warm
    path is a single DB read (it only re-hits Arena to bind an owner email that a
    prior call could not).

    Body (optional): `{"user_token": "<NetMind JWT>"}`. The frontend forwards the
    user's NetMind token so we can bind the agent's owner email via Arena's
    platform-only endpoint without an email round-trip. The token is forwarded to
    Arena and never persisted on our side. Absent token → binding is skipped.
    """
    user_id = await resolve_current_user_id(request)
    # The body is optional; tolerate empty / non-JSON without failing the call.
    user_token: str | None = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            token = body.get("user_token")
            if isinstance(token, str) and token.strip():
                user_token = token.strip()
    except Exception:
        user_token = None
    try:
        db = await get_db_client()
        from xyz_agent_context.services.arena_provisioning_service import (
            ArenaProvisioningService,
        )

        result = await ArenaProvisioningService(db).provision(user_id, user_token=user_token)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[arena.provision] failed for user {user_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Arena provisioning failed: {e}")
