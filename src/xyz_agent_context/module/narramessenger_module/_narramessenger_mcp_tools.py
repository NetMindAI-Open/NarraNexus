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
  - narra_cli(agent_id, command)               — PASSTHROUGH to the local
    ``narra-cli`` binary for query/context ops: room list/info(+members),
    im messages (history/search), im attachments download, speech,
    status. The platform injects the agent token per call; do NOT pass
    ``--token*``. ``im send`` is blocked here (use the dedicated send tools).

Transport split (transitional):
  - **Send / reply** stay Matrix-native (``narra_reply`` / ``narra_send`` /
    ``narra_send_media``, see ``_matrix_send``) — the Gateway ``/chat/send`` is
    gone, and the reply marker enables (future) progressive ``m.replace``.
  - **Query / status / roster / history / speech** go through ``narra_cli``
    (bearer via the narra-cli proxy). This replaced the old
    ``narra_status`` + ``narra_room_members`` tools (removed 2026-07-20).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule

from ._matrix_send import MatrixSendError, matrix_room_send, send_media_impl
from ._narra_command_security import sanitize_command
from ._narra_guide import get_guide
from ._narramessenger_credential_manager import NarramessengerCredentialManager
from ._narramessenger_service import do_bind
from .narra_cli_client import run_narra_cli


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
    async def narra_cli(agent_id: str, command: str) -> dict:
        """Operate NarraMessenger via the narra-cli CLI (a ready, installed tool).

        This runs narra-cli for you. Do NOT try to install narra-cli / run npm,
        and do NOT provide a token — narra-cli is already available and the
        platform injects your agent token per call (never pass ``--token`` /
        ``--token-file``). Just call this tool with the command.

        It covers rooms, history, attachments, speech, status, AND the public
        Explore timeline — including WRITES. Common commands (drop the
        ``narra-cli`` prefix):
          - explore:      ``explore publish --markdown "..."`` /
                          ``explore publish --file ./post.md`` / ``explore list`` /
                          ``explore delete --post-id <id>``. Publishing is
                          official-agents-only; a non-official agent gets an
                          ``official-agent-required`` error FROM THE SERVER —
                          that is a permission answer, not a reason to refuse
                          before trying.
          - rooms/people: ``room list`` / ``room info --room-id <id> --members``
          - history:      ``im messages --room-id <id> --limit 50``
                          (+ ``--keyword`` / ``--start`` / ``--end`` / ``--dir``)
          - attachments:  ``im attachments download --room-id <id> --event-id <e> --output ./f``
          - speech:       ``speech transcribe --input ./a.wav`` /
                          ``speech synthesize --text "..." --out ./r.wav``
          - status:       ``status``
        The ONLY things that do NOT go through this tool are CHAT messages:
        reply with ``narra_reply``; send a proactive chat message with
        ``narra_send`` / ``narra_send_media``. Everything else above is this
        tool. Call ``narra_guide(agent_id)`` for the full command reference.

        Returns ``{"success": true, "data": ...}`` or
        ``{"success": false, "error": ...}``.
        """
        if not command or not command.strip():
            return {"success": False, "error": "command is required"}
        # explore's official-agents-only policy is enforced server-side
        # (`official-agent-required`), not by our whitelist — see
        # _narra_command_security.
        # sanitize_command validates (whitelist / blocked flags / domain) AND
        # parses in one pass; a rejected or unparseable command raises ValueError.
        try:
            args = sanitize_command(command)
        except ValueError as e:
            return {"success": False, "error": "invalid_command", "message": str(e)}
        db = await XYZBaseModule.get_mcp_db_client()
        return await run_narra_cli(agent_id, args, db=db)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_guide(agent_id: str) -> dict:
        """Return the narra-cli command reference for the ``narra_cli`` tool.

        Call this before driving ``narra_cli`` for a domain you haven't used this
        session. It lists the common command shapes and — importantly — reminds
        you that narra-cli is PLATFORM-PROVIDED: never install / configure it or
        pass a token. For the exact / latest flags of any command, use
        ``narra_cli("<domain> --help")`` (that hits the live CLI).

        Returns ``{"success": true, "guide": "<markdown>"}``.
        """
        return {"success": True, "guide": get_guide()}

    logger.info(
        "NarraMessenger MCP tools registered: "
        "narra_reply, narra_send, narra_send_media, narra_bind, "
        "narra_cli, narra_guide"
    )
