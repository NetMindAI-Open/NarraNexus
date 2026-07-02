"""
@file_name: _discord_mcp_tools.py
@date: 2026-06-16
@description: Discord MCP tools — messaging-first surface + binding mgmt.

Tools exposed (8 total):
  - discord_send(agent_id, channel_id, text)             — post a message
  - discord_reply(agent_id, channel_id, message_id, text)— inline reply
  - discord_read_history(agent_id, channel_id, limit=20) — recent messages
  - discord_dm(agent_id, user_id, text)                  — proactively DM a user
  - discord_list_channels(agent_id)                      — list postable channels
  - discord_bind(agent_id, bot_token, owner_user_id="")
  - discord_status(agent_id)
  - discord_unbind(agent_id)

Unlike Slack/Telegram (which expose a generic ``*_cli`` REST dispatcher +
``*_skill`` doc loader), the Discord module is scoped messaging-first:
dedicated send/reply/read tools, no arbitrary REST passthrough and no
generated API-doc corpus. This keeps the agent-facing surface small and
the "receive a message → reply" main path rock-solid.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule

from ._discord_credential_manager import DiscordCredentialManager
from ._discord_service import _friendly_discord_error, do_bind, do_test_connection
from .discord_sdk_client import DiscordSDKClient, DiscordSDKError


async def _get_credential(agent_id: str):
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = DiscordCredentialManager(db)
    return await mgr.get(agent_id)


async def _get_manager() -> DiscordCredentialManager:
    db = await XYZBaseModule.get_mcp_db_client()
    return DiscordCredentialManager(db)


def register_discord_mcp_tools(mcp: Any) -> None:
    """Register Discord MCP tools on the given FastMCP server.

    See ``register_slack_mcp_tools`` for the note on why the caller's
    agent_id is NOT verified at this layer — the dev MCP server is
    multi-tenant and demuxes on the ``agent_id`` argument.
    """

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def discord_send(agent_id: str, channel_id: str, text: str) -> dict:
        """Send a message to a Discord channel (or DM channel).

        ``channel_id`` is the numeric Discord channel id (the inbound
        ``room_id``). ``text`` over 2000 chars is split into multiple
        messages automatically. Returns
        ``{"success": bool, "error"?: str, "data"?: {"message_id": str}}``.
        """
        if not channel_id or not text or not text.strip():
            return {"success": False, "error": "channel_id and non-empty text are required"}
        cred = await _get_credential(agent_id)
        if not cred:
            return {
                "success": False,
                "error": "no_credential",
                "hint": "no Discord bot bound; use discord_bind first",
            }
        client = DiscordSDKClient(cred.bot_token)
        try:
            msg = await client.send_message(channel_id, text)
            return {"success": True, "data": {"message_id": str(msg.get("id", ""))}}
        except DiscordSDKError as e:
            return {"success": False, "error": _friendly_discord_error(e.code or "")}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def discord_reply(
        agent_id: str, channel_id: str, message_id: str, text: str
    ) -> dict:
        """Reply inline to a specific Discord message (shows the reply arrow).

        ``message_id`` is the id of the message you're replying to (the
        inbound message's id). Falls back to a plain message if the
        referenced message was deleted. ``text`` over 2000 chars is split
        automatically.
        """
        if not channel_id or not message_id or not text or not text.strip():
            return {
                "success": False,
                "error": "channel_id, message_id and non-empty text are required",
            }
        cred = await _get_credential(agent_id)
        if not cred:
            return {
                "success": False,
                "error": "no_credential",
                "hint": "no Discord bot bound; use discord_bind first",
            }
        client = DiscordSDKClient(cred.bot_token)
        try:
            msg = await client.create_reply(channel_id, message_id, text)
            return {"success": True, "data": {"message_id": str(msg.get("id", ""))}}
        except DiscordSDKError as e:
            return {"success": False, "error": _friendly_discord_error(e.code or "")}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def discord_read_history(
        agent_id: str, channel_id: str, limit: int = 20
    ) -> dict:
        """Read recent messages from a Discord channel (chronological order).

        Returns ``{"success": bool, "data"?: [{"message_id", "author_id",
        "author_name", "content", "timestamp"}, ...]}``. ``limit`` is
        clamped to 1..100.
        """
        if not channel_id:
            return {"success": False, "error": "channel_id is required"}
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "no_credential"}
        client = DiscordSDKClient(cred.bot_token)
        raw = await client.get_channel_messages(channel_id, limit=limit)
        out = []
        for m in reversed(raw):  # REST returns newest-first → chronological
            author = m.get("author", {}) if isinstance(m, dict) else {}
            out.append(
                {
                    "message_id": str(m.get("id", "")),
                    "author_id": str(author.get("id", "")),
                    "author_name": author.get("global_name")
                    or author.get("username", ""),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", ""),
                }
            )
        return {"success": True, "data": out}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def discord_bind(
        agent_id: str, bot_token: str, owner_user_id: str = ""
    ) -> dict:
        """Bind a Discord bot to this agent.

        ``bot_token`` is the token from the Discord Developer Portal
        (Your App → Bot → Reset Token). The bot must have the
        **Message Content Intent** enabled (Bot page → Privileged Gateway
        Intents) or it will receive empty message bodies.

        ``owner_user_id`` is OPTIONAL — the numeric Discord user id of the
        agent owner (Discord → Settings → Advanced → Developer Mode, then
        right-click your name → Copy User ID). Supplying it lets the agent
        distinguish owner from stranger. Without it, every Discord sender
        is treated as untrusted.

        Returns ``{"success": bool, "error"?: str, "data"?: {...}}``.
        """
        mgr = await _get_manager()
        return await do_bind(mgr, agent_id, bot_token, owner_user_id=owner_user_id)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def discord_status(agent_id: str) -> dict:
        """Return sanitised Discord binding status (NO raw token).

        Re-runs GET /users/@me so you see live connectivity, not just DB
        state.
        """
        mgr = await _get_manager()
        cred = await mgr.get(agent_id)
        if not cred:
            return {"success": True, "data": None, "bound": False}
        live = await do_test_connection(mgr, agent_id)
        public = cred.to_public_dict()
        public["bound"] = True
        public["live_check"] = live
        return {"success": True, "data": public}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def discord_dm(agent_id: str, user_id: str, text: str) -> dict:
        """Proactively send a direct message to a Discord user by numeric id.

        Use this to START a DM with someone who hasn't messaged the bot
        first (to REPLY inside an existing DM, just use ``discord_send`` /
        ``discord_reply`` with the inbound channel id). ``user_id`` is the
        numeric Discord user id (snowflake). Internally opens a DM channel
        then sends; ``text`` over 2000 chars is split automatically.

        Caveats (surface as errors): the bot must share a server with the
        user, and the user's privacy settings must allow DMs from server
        members — otherwise Discord returns ``forbidden``.
        """
        if not user_id or not text or not text.strip():
            return {"success": False, "error": "user_id and non-empty text are required"}
        cred = await _get_credential(agent_id)
        if not cred:
            return {
                "success": False,
                "error": "no_credential",
                "hint": "no Discord bot bound; use discord_bind first",
            }
        client = DiscordSDKClient(cred.bot_token)
        try:
            channel_id = await client.create_dm_channel(user_id)
            if not channel_id:
                return {"success": False, "error": "could not open a DM channel with that user"}
            msg = await client.send_message(channel_id, text)
            return {
                "success": True,
                "data": {"channel_id": channel_id, "message_id": str(msg.get("id", ""))},
            }
        except DiscordSDKError as e:
            return {"success": False, "error": _friendly_discord_error(e.code or "")}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def discord_list_channels(agent_id: str) -> dict:
        """List the text channels the bot can post to, across its servers.

        Use this to find a ``channel_id`` to post to a SPECIFIC channel
        (e.g. an announcements channel) when you didn't get one from an
        inbound message. Returns text + announcement channels only (the
        ones a bot can send messages to).

        Returns ``{"success": bool, "data"?: [{"guild_id", "guild_name",
        "channel_id", "channel_name"}, ...]}``.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "no_credential"}
        client = DiscordSDKClient(cred.bot_token)
        guilds = await client.list_guilds()
        out: list[dict] = []
        # Discord channel types that accept messages: 0 = text, 5 = announcement.
        _POSTABLE_TYPES = {0, 5}
        for g in guilds:
            guild_id = str(g.get("id", ""))
            guild_name = g.get("name", "")
            for c in await client.list_guild_channels(guild_id):
                if c.get("type") in _POSTABLE_TYPES:
                    out.append(
                        {
                            "guild_id": guild_id,
                            "guild_name": guild_name,
                            "channel_id": str(c.get("id", "")),
                            "channel_name": c.get("name", ""),
                        }
                    )
        return {"success": True, "data": out}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def discord_unbind(agent_id: str) -> dict:
        """Remove this agent's Discord binding."""
        mgr = await _get_manager()
        removed = await mgr.unbind(agent_id)
        if not removed:
            return {"success": False, "error": "no Discord credential bound"}
        return {"success": True, "data": {"unbound": True}}

    logger.info(
        "Discord MCP tools registered: discord_send, discord_reply, "
        "discord_read_history, discord_dm, discord_list_channels, "
        "discord_bind, discord_status, discord_unbind"
    )
