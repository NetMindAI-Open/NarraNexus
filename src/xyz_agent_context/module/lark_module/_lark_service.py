"""
@file_name: _lark_service.py
@date: 2026-04-14
@description: Shared Lark business logic used by both HTTP routes and MCP tools.

Contains bind, owner resolution, and auth status helpers that must not
live in the API layer (backend/routes/) to avoid circular imports.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from ._lark_credential_manager import (
    LarkCredential,
    LarkCredentialManager,
    _encode_secret,
    AUTH_STATUS_BOT_READY,
    AUTH_STATUS_USER_LOGGED_IN,
    AUTH_STATUS_NOT_LOGGED_IN,
)
from .lark_cli_client import LarkCLIClient

# Shared CLI client (stateless, safe to share)
_cli = LarkCLIClient()

# Sentinel string from lark-cli auth status output
_LARK_NO_USERS_SENTINEL = "(no logged-in users)"


async def do_bind(
    mgr: LarkCredentialManager,
    agent_id: str,
    app_id: str,
    app_secret: str,
    brand: str,
    owner_email: str = "",
) -> dict[str, Any]:
    """Core bind logic shared between HTTP route and MCP tool.

    DB-first flow: save the credential (including workspace_path) upfront
    so `_run_with_agent_id` can find it, then verify by triggering a
    bot-info lookup which hydrates the workspace via `config init`.
    Rollback if verification fails — that keeps DB and workspace consistent.

    `brand` MUST be "feishu" or "lark" — no silent default. Caller is
    expected to have ASKED the user which platform they're on. We can't
    auto-detect: both platforms accept `cli_`-prefixed app IDs, both
    tenant_access_token endpoints cross-route via redirect, and only the
    lark_oapi WebSocket subscriber enforces domain strictly (error 1000040351
    "Incorrect domain name"). By the time we discover the mismatch the
    user is already bound and silently missing inbound messages.

    Returns {"success": True, "data": {...}} or {"success": False, "error": ...}.
    """
    from ._lark_workspace import build_profile_name, ensure_workspace

    if brand not in ("feishu", "lark"):
        return {
            "success": False,
            "error": (
                f"brand must be 'feishu' (飞书 · 中国大陆) or 'lark' "
                f"(Lark International), got {brand!r}. The caller MUST ask "
                f"the user which platform they're on — guessing from the "
                f"App ID is unreliable (both use cli_-prefixed IDs) and a "
                f"wrong brand silently breaks WebSocket event delivery."
            ),
        }

    # Check if this agent already has a bot
    existing = await mgr.get_credential(agent_id)
    if existing:
        return {"success": False, "error": "Agent already has a Lark bot bound. Unbind first."}

    # Each Lark app can only be bound to one agent
    same_app = await mgr.get_by_app_id(app_id)
    if same_app:
        other_agents = [c.agent_id for c in same_app]
        return {
            "success": False,
            "error": (
                f"App ID {app_id} is already bound to agent(s): {', '.join(other_agents)}. "
                f"Each agent needs its own Lark app."
            ),
        }

    # Fetch agent_name for a human-readable profile name (best-effort)
    agent_row = await mgr.db.get_one("agents", {"agent_id": agent_id})
    agent_name = (agent_row or {}).get("agent_name", "") or agent_id
    profile_name = build_profile_name(agent_name, agent_id)

    # Pre-create workspace so the first agent-scoped call can hydrate
    workspace = ensure_workspace(agent_id)

    # Save DB row BEFORE verification — _run_with_agent_id needs it to exist
    cred = LarkCredential(
        agent_id=agent_id,
        app_id=app_id,
        app_secret_ref=f"appsecret:{app_id}",
        app_secret_encoded=_encode_secret(app_secret),
        brand=brand,
        profile_name=profile_name,
        workspace_path=str(workspace),
        auth_status=AUTH_STATUS_BOT_READY,
    )
    await mgr.save_credential(cred)

    # Verify credentials via auth status (triggers hydrate which runs config
    # init). Bot identity has no "current user" concept — using `get-user`
    # without `--user-id` always fails. `auth status` works for bot identity
    # and is the right shape for credential validation.
    bot_info = await _cli._run_with_agent_id(["auth", "status"], agent_id)
    if not bot_info.get("success"):
        # Credentials invalid → rollback so the user can retry with correct values
        await mgr.delete_credential(agent_id)
        raw_err = bot_info.get("error", "Credential verification failed. Check app_id and app_secret.")
        # Unwrap nested error dict if present
        if isinstance(raw_err, dict):
            raw_err = raw_err.get("message", str(raw_err))
        return {
            "success": False,
            "error": raw_err,
        }

    # Fetch bot name via bot-info API (best-effort, non-fatal)
    bot_user = await _cli._run_with_agent_id(
        ["api", "GET", "/open-apis/bot/v3/info", "--as", "bot"],
        agent_id,
    )
    if bot_user.get("success"):
        bdata = bot_user.get("data", {}).get("bot", bot_user.get("data", {}))
        name = bdata.get("app_name", bdata.get("name", ""))
        if name:
            await mgr.update_bot_name(agent_id, name)

    # Resolve owner identity from email
    owner_open_id = ""
    owner_name = ""
    if owner_email:
        owner_open_id, owner_name = await resolve_owner(agent_id, owner_email)
        if owner_open_id:
            await mgr.update_owner(agent_id, owner_open_id, owner_name)

    return {
        "success": True,
        "data": {
            "profile_name": profile_name,
            "brand": brand,
            "app_id": app_id,
            "auth_status": AUTH_STATUS_BOT_READY,
            "owner_open_id": owner_open_id,
            "owner_name": owner_name,
        },
    }


async def resolve_owner(agent_id: str, owner_email: str) -> tuple[str, str]:
    """Resolve owner Lark identity from email. Returns (open_id, name).

    Uses the agent-scoped runner (HOME isolation) with fallback to --profile.
    """
    if not owner_email:
        return "", ""

    owner_open_id = ""
    owner_name = ""

    lookup = await _cli._run_with_agent_id(
        ["api", "POST", "/open-apis/contact/v3/users/batch_get_id",
         "--data", json.dumps({"emails": [owner_email]})],
        agent_id=agent_id,
    )
    if lookup.get("success"):
        user_list = lookup.get("data", {}).get("data", {}).get("user_list", [])
        if user_list:
            owner_open_id = user_list[0].get("user_id", "")

    if owner_open_id:
        user_info = await _cli._run_with_agent_id(
            ["contact", "+get-user", "--as", "bot", "--user-id", owner_open_id],
            agent_id=agent_id,
        )
        if user_info.get("success"):
            udata = user_info.get("data", {})
            user_obj = udata.get("user", udata)
            owner_name = user_obj.get("name", user_obj.get("en_name", ""))
        if not owner_name:
            owner_name = owner_email.split("@")[0].replace(".", " ").title()

    return owner_open_id, owner_name


def determine_auth_status(auth_data: dict) -> str:
    """Determine auth status from lark-cli auth status response data.

    Returns:
        - "user_logged_in" if user tokens exist (user OAuth completed)
        - "bot_ready" if only bot identity available
        - "not_logged_in" if neither
    """
    identity = auth_data.get("identity", "")
    users = auth_data.get("users", auth_data.get("userName", ""))
    token_status = auth_data.get("tokenStatus", "")

    # User tokens present → full OAuth done
    if identity == "user" or token_status == "valid":
        return AUTH_STATUS_USER_LOGGED_IN
    if users and users != _LARK_NO_USERS_SENTINEL:
        return AUTH_STATUS_USER_LOGGED_IN

    # Bot identity available → bot ready
    if identity == "bot":
        return AUTH_STATUS_BOT_READY

    return AUTH_STATUS_NOT_LOGGED_IN
