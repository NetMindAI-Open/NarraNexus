"""
@file_name: _discord_service.py
@date: 2026-06-16
@description: Shared bind/test helpers for the Discord channel.

Both REST routes (`backend/routes/discord.py`) and MCP tools
(`_discord_mcp_tools.py`) call into here so bind / test logic lives in
one place. Pattern mirrors `slack_module/_slack_service.py`.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from ._discord_credential_manager import DiscordCredentialManager
from .discord_sdk_client import DiscordSDKClient, DiscordSDKError


# Map raw Discord error codes to actionable English. Anything not in the
# map falls through with the raw code.
_DISCORD_FRIENDLY_ERRORS: dict[str, str] = {
    "unauthorized": "Bot Token is invalid or was reset — copy a fresh token from the Discord Developer Portal (Your App → Bot → Reset Token) and re-bind.",
    "forbidden": "The bot lacks permission for that action, or isn't in the target server/channel. Re-invite it with the correct OAuth2 scopes (bot) and permissions.",
    "not_found": "Channel or user not found. Double-check the id, or invite the bot to the server first.",
    "rate_limited": "Discord is rate-limiting requests right now. Wait a moment and try again.",
}


def _friendly_discord_error(code: str) -> str:
    return _DISCORD_FRIENDLY_ERRORS.get(code, f"Discord returned error: {code}")


async def do_bind(
    mgr: DiscordCredentialManager,
    agent_id: str,
    bot_token: str,
    owner_user_id: str = "",
) -> dict[str, Any]:
    """Bind a Discord bot to an agent. Returns ``{success, error?, data?}``."""
    return await mgr.bind(agent_id, bot_token, owner_user_id=owner_user_id)


async def do_test_connection(
    mgr: DiscordCredentialManager,
    agent_id: str,
) -> dict[str, Any]:
    """Verify the stored bot token still works by re-running GET /users/@me.

    Catches token resets on the Discord side that a pure DB lookup misses.
    Refreshes the stored bot identity if it changed. Returns the same
    envelope shape as ``do_bind``.
    """
    cred = await mgr.get(agent_id)
    if not cred:
        return {"success": False, "error": "no Discord credential bound for this agent"}

    client = DiscordSDKClient(cred.bot_token)
    try:
        me = await client.get_bot_user()
    except DiscordSDKError as e:
        logger.warning(f"[discord:{agent_id}] connection test failed: {e.code}")
        return {"success": False, "error": _friendly_discord_error(e.code or "")}

    bot_user_id = str(me.get("id", ""))
    bot_username = me.get("global_name") or me.get("username", "") or ""
    # Keep stored identity fresh (owner may have renamed the bot).
    await mgr.update_bot_identity(
        agent_id, bot_username=bot_username, bot_user_id=bot_user_id
    )
    return {
        "success": True,
        "data": {"bot_user_id": bot_user_id, "bot_username": bot_username},
    }
