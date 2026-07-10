"""
@file_name: _narramessenger_mcp_tools.py
@date: 2026-07-02
@description: NarraMessenger MCP tools — the agent-facing reply/send/bind surface.

Tools exposed:
  - narra_reply(agent_id, text)                — REPLY to the message you were
    invoked on. A marker: your ``text`` is delivered to the room automatically
    by the trigger (Matrix ``room_send``) once the turn ends. No room_id needed.
    The room stays quiet until this fires — there is no intermediate status
    surface (the previous ``narra_progress`` tool was removed 2026-07-08 with
    the placeholder-free UX refactor).
  - narra_send(agent_id, room_id, text)        — PROACTIVE text send to a room
    via Matrix ``room_send``. Use when you are NOT replying to an inbound
    message (e.g. a Job / scheduled push).
  - narra_send_media(agent_id, room_id, file_path, caption?) — send an image /
    file / audio / video from your workspace: uploads to the homeserver media
    repo then ``room_send``s an ``m.image`` / ``m.file`` / … event.
  - narra_bind(agent_id, bind_command)         — bind this agent to NarraMessenger
    from a pasted bind link (drives the bind + writes the credential).
  - narra_status(agent_id)                     — sanitised binding status + live
    ``/status`` check.
  - narra_room_members(agent_id, room_id)      — live roster fetch via
    ``GET /_matrix/client/v3/rooms/{room_id}/joined_members``. Kept as a
    tool (not baked into prompt) so the agent only pays for the roster
    when it actually needs to know "who's in this room" — the vast
    majority of turns don't. Added 2026-07-02 alongside the Direct
    Matrix migration.

Outbound is Matrix-native (``room_send`` / media upload, see ``_matrix_send``),
NOT the Gateway ``/chat/send`` — the transport is Matrix now, and ``/chat/send``
can carry neither media nor (future) progressive ``m.replace`` streaming.
"""

from __future__ import annotations

from typing import Any

import aiohttp
from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule

from ._matrix_send import MatrixSendError, matrix_room_send, send_media_impl
from ._narramessenger_client import NarramessengerAPIError, NarramessengerClient
from ._narramessenger_credential_manager import NarramessengerCredentialManager
from ._narramessenger_service import do_bind


async def _get_credential(agent_id: str):
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = NarramessengerCredentialManager(db)
    return await mgr.get(agent_id)


async def _get_owner(agent_id: str) -> str:
    """Resolve the agent's OWNER user_id (``agents.created_by``) — the
    workspace root that ``narra_send_media`` reads files from."""
    db = await XYZBaseModule.get_mcp_db_client()
    row = await db.get_one("agents", {"agent_id": agent_id})
    return (row or {}).get("created_by", "") or ""


def register_narramessenger_mcp_tools(mcp: Any) -> None:
    """Register NarraMessenger MCP tools on the given FastMCP server."""

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_reply(agent_id: str, text: str) -> dict:
        """Reply to the NarraMessenger message you were invoked on.

        ``text`` is your real, user-facing answer (plain text / markdown).
        You do NOT pass a room_id: this is a marker — the NarraMessenger
        trigger delivers your reply to the originating room automatically
        (via Matrix ``room_send``) once this turn ends. Use this to REPLY.

        For a proactive message (not a reply to an inbound message) use
        ``narra_send``; to attach an image/file use ``narra_send_media``.

        Returns ``{"ok": true}``, else ``{"ok": false, "error": ...}``.
        """
        if not text or not text.strip():
            return {"ok": False, "error": "non-empty text is required"}
        # Marker only — the actual send happens in the trigger's
        # extract_output → _send_matrix_reply. The reply text rides in this
        # tool call's arguments, which the runtime records for the trigger
        # to read. Owning delivery in the trigger is what makes future
        # progressive m.replace streaming possible.
        return {"ok": True}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_send(agent_id: str, room_id: str, text: str) -> dict:
        """PROACTIVELY send a text message to a NarraMessenger room (NOT a reply).

        ``room_id`` is a Matrix room id (e.g. ``!abc:matrix.netmind.chat``).
        Use this only when you are sending on your own behalf — e.g. from a
        Job, a scheduled task, or following up after finishing long work —
        NOT when replying to a message you were invoked on (use ``narra_reply``
        for that).

        Delivered natively via Matrix ``room_send``. Returns
        ``{"ok": true, "event_id": ...}`` on success.
        """
        if not room_id or not text or not text.strip():
            return {"ok": False, "error": "room_id and non-empty text are required"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no NarraMessenger binding for this agent"}
        if not cred.matrix_access_token or not cred.matrix_homeserver_url:
            return {"ok": False, "error": "no_matrix_credentials",
                    "hint": "credential is not on the Matrix transport"}

        try:
            event_id = await matrix_room_send(
                homeserver=cred.matrix_homeserver_url,
                token=cred.matrix_access_token,
                room_id=room_id,
                content={"msgtype": "m.text", "body": text},
            )
            return {"ok": True, "event_id": event_id}
        except MatrixSendError as e:
            return {"ok": False, "error": e.code, "message": str(e)}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_send_media(
        agent_id: str, room_id: str, file_path: str, caption: str = ""
    ) -> dict:
        """Send an image / file / audio / video from your workspace to a room.

        Put the file in your workspace first, then call this with the path
        (relative to your workspace root). ``caption`` is optional text shown
        with the media. The file is uploaded to the homeserver's media repo
        and sent as an ``m.image`` / ``m.file`` / ``m.audio`` / ``m.video``
        event; the recipient sees it inline in NarraMessenger.

        Only files inside your own workspace can be sent. Returns
        ``{"ok": true, "event_id", "mxc", "msgtype"}`` or
        ``{"ok": false, "error": ...}``.
        """
        if not room_id or not file_path:
            return {"ok": False, "error": "room_id and file_path are required"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no NarraMessenger binding for this agent"}
        if not cred.matrix_access_token or not cred.matrix_homeserver_url:
            return {"ok": False, "error": "no_matrix_credentials",
                    "hint": "credential is not on the Matrix transport"}

        from backend.config import settings as backend_settings

        owner_id = await _get_owner(agent_id) or agent_id
        return await send_media_impl(
            agent_id=agent_id,
            owner_id=owner_id,
            homeserver=cred.matrix_homeserver_url,
            token=cred.matrix_access_token,
            room_id=room_id,
            file_path=file_path,
            max_bytes=backend_settings.max_upload_bytes,
            caption=caption or None,
        )

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

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_room_members(agent_id: str, room_id: str) -> dict:
        """List the joined members of a NarraMessenger room.

        Live GET to the Matrix homeserver's
        ``/_matrix/client/v3/rooms/{room_id}/joined_members`` endpoint,
        using the Matrix access token stored on the credential (NOT the
        Narra bearer — Matrix rejects the Narra bearer with
        ``M_UNKNOWN_TOKEN``).

        Use this when you need to know WHO is in a group room — e.g. to
        @-mention someone specific, to answer "who's in this room?", or
        to plan a message to a particular subset of members. This is
        NOT auto-injected into every turn's prompt (would cost too many
        tokens on large groups); the agent calls it on demand.

        Returns:
            {"ok": true, "members": [
                {"user_id": "@alice:h", "display_name": "Alice",
                 "avatar_url": "mxc://..."},
                ...
            ]}
            or {"ok": false, "error": <code>}.
        """
        if not room_id:
            return {"ok": False, "error": "room_id is required"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no NarraMessenger binding for this agent"}
        if not cred.matrix_access_token or not cred.matrix_homeserver_url:
            return {"ok": False, "error": "no_matrix_credentials",
                    "hint": "credential is not on the Matrix transport"}

        url = (
            f"{cred.matrix_homeserver_url.rstrip('/')}"
            f"/_matrix/client/v3/rooms/{room_id}/joined_members"
        )
        headers = {"Authorization": f"Bearer {cred.matrix_access_token}"}
        timeout = aiohttp.ClientTimeout(total=10.0)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status < 200 or resp.status >= 300:
                        # Matrix errors surface with a JSON body carrying
                        # `errcode`; propagate for observability.
                        try:
                            body = await resp.json()
                        except Exception:  # noqa: BLE001
                            body = {}
                        return {
                            "ok": False,
                            "error": body.get("errcode") or f"http_{resp.status}",
                            "message": body.get("error") or "",
                        }
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            return {"ok": False, "error": "transport_error",
                    "message": f"{type(e).__name__}: {e}"}

        # Matrix returns {"joined": {mxid: {display_name, avatar_url}}}
        joined = data.get("joined") or {}
        members = [
            {
                "user_id": mxid,
                "display_name": info.get("display_name") or mxid,
                "avatar_url": info.get("avatar_url") or "",
            }
            for mxid, info in joined.items()
        ]
        return {"ok": True, "members": members, "count": len(members)}

    logger.info(
        "NarraMessenger MCP tools registered: "
        "narra_reply, narra_progress, narra_send, narra_send_media, "
        "narra_bind, narra_status, narra_room_members"
    )
