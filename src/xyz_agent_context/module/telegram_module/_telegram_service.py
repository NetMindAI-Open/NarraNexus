"""
@file_name: _telegram_service.py
@date: 2026-05-09
@description: Shared bind/test helpers for Telegram channel.

Both REST routes (`backend/routes/telegram.py`) and MCP tools
(`_telegram_mcp_tools.py`) call into here so bind / test logic lives in
one place. Pattern mirrors `lark_module/_lark_service.py` and
`slack_module/_slack_service.py`.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ._telegram_credential_manager import TelegramCredentialManager
from .telegram_sdk_client import TelegramSDKClient, TelegramSDKError


# Telegram surfaces ``description`` strings rather than stable codes —
# match by substring so we catch variants like
# ``"Unauthorized"`` vs ``"Unauthorized: bot was kicked"``. Falls back to
# the raw description for anything not on the list.
def _friendly_telegram_error(code: str) -> str:
    lower = (code or "").lower()
    if "unauthorized" in lower:
        return (
            "Bot Token rejected by Telegram. Re-copy from @BotFather "
            "(it might have been revoked via /revoke) and re-bind."
        )
    if "forbidden" in lower and "kicked" in lower:
        return "The bot was kicked from this chat. Re-invite it from inside Telegram."
    if "forbidden" in lower and "blocked" in lower:
        return "The user has blocked the bot. They must unblock it in Telegram before the bot can DM them."
    if "chat not found" in lower:
        return "Chat not found. Double-check the chat id, or invite the bot to a chat it isn't in yet."
    if "conflict" in lower:
        return "Another process is polling this bot. Stop the other process or call deleteWebhook, then retry."
    if "too many requests" in lower or "flood" in lower:
        return "Telegram is rate-limiting requests. Wait a minute and try again."
    if not code:
        return "Telegram returned an unknown error."
    return f"Telegram returned error: {code}"


async def do_bind(
    mgr: TelegramCredentialManager,
    agent_id: str,
    bot_token: str,
    owner_username: str = "",
) -> dict[str, Any]:
    """Bind a Telegram bot to an agent. Returns ``{success, error?, data?}``."""
    return await mgr.bind(agent_id, bot_token, owner_username)


async def do_test_connection(
    mgr: TelegramCredentialManager,
    agent_id: str,
) -> dict[str, Any]:
    """Re-run getMe against the stored credential.

    Catches token revocation at @BotFather (``/revoke``) that wouldn't
    show up in a pure DB lookup.
    """
    cred = await mgr.get(agent_id)
    if not cred:
        return {"success": False, "error": "no Telegram credential bound for this agent"}

    client = TelegramSDKClient(cred.bot_token)
    try:
        info = await client.get_me()
    except TelegramSDKError as e:
        logger.warning(f"[telegram:{agent_id}] connection test failed: {e.code}")
        return {"success": False, "error": _friendly_telegram_error(e.code or "")}
    finally:
        await client.close()

    # Refresh ``bot_username`` on success — owners can rename their bot in
    # @BotFather after bind, which would otherwise leave the stored
    # username stale and the "pending owner" UI hint pointing to a
    # non-existent handle.
    fresh_username = info.get("username", "") or ""
    if fresh_username and fresh_username != cred.bot_username:
        try:
            await mgr.update_bot_identity(
                agent_id, bot_username=fresh_username,
            )
        except Exception as e:  # noqa: BLE001 — best-effort refresh
            logger.warning(
                f"[telegram:{agent_id}] bot_username refresh failed: "
                f"{type(e).__name__}: {e}"
            )

    return {
        "success": True,
        "data": {
            "bot_user_id": str(info.get("id", "")),
            "bot_username": fresh_username,
            "first_name": info.get("first_name", ""),
        },
    }
