"""
@file_name: lark.py
@date: 2026-04-10
@description: Backend API routes for Lark/Feishu bot binding, auth, and management.

Endpoints:
  POST   /api/lark/bind          — Bind a Lark bot to an agent
  POST   /api/lark/auth/login    — Initiate OAuth login (returns auth URL)
  POST   /api/lark/auth/complete — Complete OAuth with device code
  GET    /api/lark/auth/status   — Check login status
  POST   /api/lark/test          — Test connection
  DELETE /api/lark/unbind        — Unbind a Lark bot
  GET    /api/lark/credential    — Get credential info for an agent
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from loguru import logger

from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredentialManager,
)
from xyz_agent_context.module.lark_module._lark_service import (
    do_bind,
    determine_auth_status,
)
from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient

router = APIRouter()
_cli = LarkCLIClient()

# Pattern for safe agent_id / app_id values (alphanumeric + underscore + hyphen)
_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"
# Device code pattern (alphanumeric + common separators)
_DEVICE_CODE_PATTERN = r"^[a-zA-Z0-9_\-\.]{1,256}$"


# =========================================================================
# Request / Response schemas
# =========================================================================

class BindRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)
    app_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)
    app_secret: str = Field(min_length=1, max_length=256)
    brand: str = "feishu"  # "feishu" or "lark"
    owner_email: str = Field(default="", max_length=254)


class AgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)


class AuthCompleteRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)
    device_code: str = Field(min_length=1, max_length=256, pattern=_DEVICE_CODE_PATTERN)


# =========================================================================
# Helper
# =========================================================================

async def _get_db():
    """Get database client via factory (same pattern as other routes)."""
    from xyz_agent_context.utils.db_factory import get_db_client
    return await get_db_client()


async def _verify_agent_ownership(request: Request, agent_id: str) -> str | None:
    """Verify that the caller owns the agent. Returns error message or None.

    In local mode (no JWT), ownership is not enforced.
    In cloud mode, the agent's created_by must match the JWT user_id.
    """
    if not hasattr(request.state, "user_id") or not request.state.user_id:
        return None  # Local mode — no auth enforcement
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
async def bind_lark_bot(request: Request, body: BindRequest) -> dict[str, Any]:
    """Bind a Lark/Feishu bot to an agent."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    if body.brand not in ("feishu", "lark"):
        return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

    # Validate owner_email format if provided
    if body.owner_email and "@" not in body.owner_email:
        return {"success": False, "error": "Invalid email format for owner_email."}

    db = await _get_db()
    mgr = LarkCredentialManager(db)

    # Core bind logic (shared with MCP tool via _lark_service)
    bind_result = await do_bind(mgr, body.agent_id, body.app_id, body.app_secret, body.brand)
    if not bind_result["success"]:
        return bind_result

    logger.info(f"Lark bot bound: agent={body.agent_id}, app_id={body.app_id}")
    return bind_result


@router.post("/auth/login")
async def lark_auth_login(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Initiate OAuth login. Returns auth URL for browser authorization."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    # V2: use workspace-based runner
    result = await _cli._run_with_agent_id(
        ["auth", "login", "--recommend", "--json", "--no-wait"],
        agent_id=body.agent_id,
        timeout=60.0,
    )
    return result


@router.post("/auth/complete")
async def lark_auth_complete(request: Request, body: AuthCompleteRequest) -> dict[str, Any]:
    """Complete OAuth login with device code from a previous --no-wait call."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    # V2: use workspace-based runner
    result = await _cli._run_with_agent_id(
        ["auth", "login", "--device-code", body.device_code, "--json"],
        agent_id=body.agent_id,
        timeout=60.0,
    )

    # Update auth status on success
    if result.get("success"):
        from xyz_agent_context.module.lark_module._lark_credential_manager import AUTH_STATUS_USER_LOGGED_IN
        await mgr.update_auth_status(body.agent_id, AUTH_STATUS_USER_LOGGED_IN)

        # Try to get bot name via bot-info API (bot identity has no "self"
        # user concept, so `+get-user --as bot` fails without --user-id).
        bot_info = await _cli._run_with_agent_id(
            ["api", "GET", "/open-apis/bot/v3/info", "--as", "bot"],
            agent_id=body.agent_id,
        )
        if bot_info.get("success"):
            bdata = bot_info.get("data", {}).get("bot", bot_info.get("data", {}))
            name = bdata.get("app_name", bdata.get("name", ""))
            if name:
                await mgr.update_bot_name(body.agent_id, name)

    return result


@router.get("/auth/status")
async def lark_auth_status(request: Request, agent_id: str) -> dict[str, Any]:
    """Check the authentication status of the bound bot."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    result = await _cli._run_with_agent_id(["auth", "status"], agent_id=agent_id)

    # Sync auth status to DB
    if result.get("success"):
        data = result.get("data", {})
        new_status = determine_auth_status(data)
        if new_status != cred.auth_status:
            await mgr.update_auth_status(agent_id, new_status)
        data["db_auth_status"] = new_status

    return result


@router.post("/test")
async def test_lark_connection(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Test connection by getting bot's own info."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    return await _cli._run_with_agent_id(
        ["api", "GET", "/open-apis/bot/v3/info", "--as", "bot"],
        agent_id=body.agent_id,
    )


@router.delete("/unbind")
async def unbind_lark_bot(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Unbind Lark bot from agent. Removes CLI profile and DB record."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    # Remove CLI profile (keychain entry + workspace config.json) then
    # nuke the workspace directory itself.
    try:
        await _cli.profile_remove(body.agent_id)
    except Exception:
        pass  # Best-effort — workspace may already be gone
    from xyz_agent_context.module.lark_module._lark_workspace import cleanup_workspace
    cleanup_workspace(body.agent_id)

    # Remove DB record
    await mgr.delete_credential(body.agent_id)

    # Clean up Inbox data: remove this agent from all lark_ channels
    try:
        all_members = await db.get("bus_channel_members", {"agent_id": body.agent_id})
        lark_channel_ids = [
            m["channel_id"] for m in all_members
            if m.get("channel_id", "").startswith("lark_")
        ]
        for cid in lark_channel_ids:
            await db.delete("bus_channel_members", {
                "channel_id": cid, "agent_id": body.agent_id
            })
            remaining = await db.get("bus_channel_members", {"channel_id": cid})
            if not remaining:
                await db.delete("bus_messages", {"channel_id": cid})
                await db.delete("bus_channels", {"channel_id": cid})
    except Exception as e:
        logger.warning(f"Failed to clean up Lark inbox data: {e}")

    logger.info(f"Lark bot unbound: agent={body.agent_id}")
    return {"success": True}


@router.get("/credential")
async def get_lark_credential(request: Request, agent_id: str) -> dict[str, Any]:
    """Get Lark credential info for an agent (no secrets exposed)."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(agent_id)

    if not cred:
        return {"success": True, "data": None}

    return {
        "success": True,
        "data": {
            "agent_id": cred.agent_id,
            "app_id": cred.app_id,
            "brand": cred.brand,
            "bot_name": cred.bot_name,
            "owner_open_id": cred.owner_open_id,
            "owner_name": cred.owner_name,
            "auth_status": cred.auth_status,
            "is_active": cred.is_active,
        },
    }
