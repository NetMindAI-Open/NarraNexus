"""
@file_name: _slack_service.py
@date: 2026-05-08
@description: Shared bind/test helpers for Slack channel.

Both REST routes (`backend/routes/slack.py`) and MCP tools
(`_slack_mcp_tools.py`) call into here so bind / test logic lives in one
place. Pattern mirrors `lark_module/_lark_service.py`.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ._slack_credential_manager import SlackCredentialManager
from .slack_sdk_client import SlackSDKClient, SlackSDKError


# Map raw Slack error codes to actionable English. Anything not in the
# map falls through with the raw code (the agent / power user can still
# google it; we just hide the most opaque-looking strings from
# first-time users).
_SLACK_FRIENDLY_ERRORS: dict[str, str] = {
    "invalid_auth": "Bot Token is invalid or revoked — copy a fresh xoxb-… from your Slack app's OAuth page and re-bind.",
    "token_revoked": "Bot Token was revoked by the workspace admin. Re-install the app in Slack to mint a new token, then re-bind.",
    "token_expired": "Bot Token expired. Re-install the Slack app, copy the new xoxb-… token, and re-bind.",
    "account_inactive": "The Slack workspace this token belongs to is disabled. Re-activate the workspace or use a different one.",
    "not_authed": "No authentication credentials were sent — the token field is probably empty. Re-bind with both xoxb-… and xapp-… filled in.",
    "missing_scope": "The Slack app is missing an OAuth scope. Re-create the app from our manifest YAML (it pre-configures all required scopes), then re-bind.",
    "not_in_channel": "The bot isn't a member of that channel. Invite it from Slack: /invite @<your-bot-name>.",
    "channel_not_found": "Channel not found. Double-check the channel ID, or invite the bot to a channel it isn't in yet.",
    "rate_limited": "Slack is rate-limiting requests right now. Wait a minute and try again.",
}


def _friendly_slack_error(code: str) -> str:
    return _SLACK_FRIENDLY_ERRORS.get(code, f"Slack returned error: {code}")


async def do_bind(
    mgr: SlackCredentialManager,
    agent_id: str,
    bot_token: str,
    app_token: str,
    owner_email: str = "",
) -> dict[str, Any]:
    """Bind a Slack workspace to an agent. Returns ``{success, error?, data?}``."""
    return await mgr.bind(agent_id, bot_token, app_token, owner_email)


async def do_test_connection(
    mgr: SlackCredentialManager,
    agent_id: str,
) -> dict[str, Any]:
    """Verify the stored bot token still works by re-running ``auth.test``.

    This catches token revocation at the Slack side that wouldn't show up
    in a pure DB lookup. Returns the same envelope as ``do_bind``.
    """
    cred = await mgr.get(agent_id)
    if not cred:
        return {"success": False, "error": "no Slack credential bound for this agent"}

    client = SlackSDKClient(cred.bot_token)
    try:
        info = await client.auth_test()
    except SlackSDKError as e:
        logger.warning(f"[slack:{agent_id}] connection test failed: {e.code}")
        return {"success": False, "error": _friendly_slack_error(e.code or "")}

    return {
        "success": True,
        "data": {
            "team_id": info.get("team_id", ""),
            "team_name": info.get("team", ""),
            "bot_user_id": info.get("user_id", ""),
            "bot_name": info.get("user", ""),
        },
    }
