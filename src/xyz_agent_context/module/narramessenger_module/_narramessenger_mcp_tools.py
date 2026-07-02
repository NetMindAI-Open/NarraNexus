"""
@file_name: _narramessenger_mcp_tools.py
@date: 2026-06-17
@description: NarraMessenger MCP tools — the agent-facing reply/send/bind surface.

Tools exposed:
  - narra_reply(agent_id, invocation_id, text) — reply to the message you were
    invoked on. Goes through ``/invocations/{id}/reply``: delivers AND closes
    the invocation (so the platform does NOT fire the 15-min timeout). This is
    the reply path; ``invocation_id`` is given to you in the turn's context.
  - narra_send(agent_id, room_id, text)        — PROACTIVE send to a room via
    ``/chat/send`` (no invocation to close, no time limit). Use this when you
    are NOT replying to an inbound message (e.g. a Job/scheduled push).
  - narra_bind(agent_id, bind_command)         — bind this agent to NarraMessenger
    from a pasted bind link (drives the Gateway bind + writes the credential).
  - narra_status(agent_id)                     — sanitised binding status + live
    ``/status`` check.
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule

from ._narramessenger_client import NarramessengerAPIError, NarramessengerClient
from ._narramessenger_credential_manager import NarramessengerCredentialManager
from ._narramessenger_service import do_bind


async def _get_credential(agent_id: str):
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = NarramessengerCredentialManager(db)
    return await mgr.get(agent_id)


def register_narramessenger_mcp_tools(mcp: Any) -> None:
    """Register NarraMessenger MCP tools on the given FastMCP server."""

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_reply(agent_id: str, invocation_id: str, text: str) -> dict:
        """Reply to the NarraMessenger message you were invoked on.

        ``invocation_id`` is provided in this turn's context (the identity
        block). ``text`` is your real, user-facing answer in plain text.

        This delivers your message AND closes the invocation, so the sender
        does NOT see a "timed out" error. Use this to REPLY. For proactive
        sends (not in response to a message), use ``narra_send`` instead.

        Returns ``{"ok": true}`` on success, else ``{"ok": false, "error": ...}``.
        """
        if not invocation_id or not text or not text.strip():
            return {"ok": False, "error": "invocation_id and non-empty text are required"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no NarraMessenger binding for this agent"}

        client = NarramessengerClient(cred.bearer_token, cred.backend_base_url)
        try:
            await client.reply(invocation_id=invocation_id, text=text)
            return {"ok": True}
        except NarramessengerAPIError as e:
            return {"ok": False, "error": e.code, "status": e.status}
        finally:
            await client.close()

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_send(agent_id: str, room_id: str, text: str) -> dict:
        """PROACTIVELY send a message to a NarraMessenger room (NOT a reply).

        ``room_id`` is a Matrix room id (e.g. ``!abc:matrix.netmind.chat``).
        Use this only when you are sending on your own behalf — e.g. from a
        Job, a scheduled task, or following up after finishing long work —
        NOT when replying to a message you were invoked on (use ``narra_reply``
        for that, so the invocation gets closed).

        Returns ``{"ok": true, "event_id": ...}`` on success.
        """
        if not room_id or not text or not text.strip():
            return {"ok": False, "error": "room_id and non-empty text are required"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no NarraMessenger binding for this agent"}

        client = NarramessengerClient(cred.bearer_token, cred.backend_base_url)
        try:
            data = await client.chat_send(
                room_id=room_id, text=text, txn_id=str(uuid.uuid4())
            )
            return {"ok": True, "event_id": data.get("event_id")}
        except NarramessengerAPIError as e:
            return {"ok": False, "error": e.code, "status": e.status}
        finally:
            await client.close()

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_bind(agent_id: str, bind_command: str) -> dict:
        """Bind this agent to NarraMessenger from a pasted bind command/link.

        The owner gets the bind command from the NarraMessenger app
        (My Space → My Agents → Bind Agents) and pastes it. Pass that whole
        string here; this drives the Gateway bind and saves the credential.
        Once it returns success, real-time receiving starts automatically.

        Returns ``{"success": true, "data": {...}}`` or
        ``{"success": false, "error": ...}``.
        """
        if not bind_command or not bind_command.strip():
            return {"success": False, "error": "bind_command is required"}
        db = await XYZBaseModule.get_mcp_db_client()
        return await do_bind(db, agent_id, bind_command)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_status(agent_id: str) -> dict:
        """Return sanitised NarraMessenger binding status (NO bearer token),
        plus a live ``/status`` check of the backend."""
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": True, "data": None, "bound": False}

        live: dict[str, Any]
        client = NarramessengerClient(cred.bearer_token, cred.backend_base_url)
        try:
            live = await client.status()
        except NarramessengerAPIError as e:
            live = {"error": e.code, "status": e.status}
        finally:
            await client.close()

        public = cred.to_public_dict()
        public["bound"] = True
        public["live_check"] = live
        return {"success": True, "data": public}

    logger.info(
        "NarraMessenger MCP tools registered: "
        "narra_reply, narra_send, narra_bind, narra_status"
    )
