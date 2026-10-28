"""
@file_name: inbox.py
@author: NexusAgent
@date: 2026-04-09
@description: Agent Inbox API — the record of conversations the user was not in

Endpoints:
  GET  /api/agent-inbox                          — list threads with messages
  PUT  /api/agent-inbox/{message_id}/read        — mark a single message read
  POST /api/agent-inbox/rooms/{room_id}/read     — mark a whole thread read

The room-level endpoint exists because the list caps each thread at 50
messages while `unread_count` is computed against ALL of them, so marking only
the latest VISIBLE message leaves any older-unread tail behind. Click-the-row
UX (2026-05-28) calls the room-level endpoint so the badge always disappears.

READS THE INBOX'S OWN TABLES (2026-08-17). It used to list every bus channel
the agent belonged to, which mixed three unrelated things into one panel — IM
conversations, agent-to-agent DMs, and team rooms that already have their own
UI — and made the panel's "mark read" button advance the SAME cursor the agent's
turn context is gated on. Clicking "read" in the panel changed what the agent
was handed next turn.

Now: `inbox_threads.last_read_at` is the USER's read state and touches nothing
the agent sees. Team rooms and the owner's own chat are deliberately absent —
the user is a live participant there and those surfaces carry their own unread
signal (`TeamWithMembers.last_message_at/preview/author`, and the chat window
itself). The rule is one sentence: the inbox is the conversations you were not
in.
"""

from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from backend.routes._ownership import assert_owned
from narranexus.platform.channel.inbox_recorder import OUTBOUND

router = APIRouter()

#: How many messages per thread the list carries. `unread_count` is computed
#: against ALL of them, which is why the room-level mark-read exists.
MESSAGES_PER_THREAD = 50


def _to_iso(value: Any) -> str:
    """Normalise timestamps (datetime / str / None) to an ISO 8601 string.

    aiomysql returns DATETIME(6) columns as `datetime.datetime` while the
    SQLite backend returns them as strings, and the default cursor fallback is
    the literal ``"1970-01-01"``. Comparing these mixed types raises
    `TypeError`. ISO 8601 strings sort lexicographically in time order, so
    normalising gives one comparable type across every backend and code path.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _get_db():
    from narranexus.platform.utils.db.db_factory import get_db_client
    return await get_db_client()


@router.get("")
async def get_agent_inbox(
    request: Request,
    agent_id: str = Query(...),
    is_read: bool | None = Query(None),
    limit: int | None = Query(None),
):
    """List this agent's inbox threads, newest activity first.

    ``is_read`` filters the messages inside each thread (None = all).
    ``limit`` overrides the per-thread message cap; negative means "no cap".
    """
    await assert_owned(request, agent_id)
    try:
        db = await _get_db()

        threads = await db.get("inbox_threads", {"agent_id": agent_id})
        if not threads:
            return {"success": True, "rooms": [], "total_unread": 0}

        # Resolve the agent's display name ONCE — every thread here belongs to
        # this one agent_id, so members[0] is always the same agent. Without this
        # the panel shows the raw `agent_<hex>` id in that slot (the counterpart
        # slot uses the stored counterpart_name and was already fine).
        _agent_row = await db.get_one("agents", {"agent_id": agent_id})
        agent_display = (_agent_row or {}).get("agent_name") or agent_id

        per_thread = MESSAGES_PER_THREAD
        if limit is not None:
            per_thread = 9999 if limit < 0 else limit

        total_unread = 0
        rooms = []

        for thread in threads:
            thread_id = thread["thread_id"]
            cursor = _to_iso(thread.get("last_read_at") or "1970-01-01")

            msg_rows = await db.get(
                "inbox_thread_messages",
                {"thread_id": thread_id},
                limit=per_thread,
                # The writer stamps a turn's inbound and reply one microsecond
                # apart, so created_at orders a turn correctly on its own.
                order_by="created_at DESC",
            )
            msg_rows = list(reversed(msg_rows))

            # Unread = arrived after the cursor and not written by the agent.
            unread = sum(
                1 for m in msg_rows
                if m.get("direction") != OUTBOUND
                and _to_iso(m.get("created_at")) > cursor
            )
            total_unread += unread

            if is_read is not None:
                if is_read:
                    msg_rows = [
                        m for m in msg_rows
                        if _to_iso(m.get("created_at")) <= cursor
                        or m.get("direction") == OUTBOUND
                    ]
                else:
                    msg_rows = [
                        m for m in msg_rows
                        if m.get("direction") != OUTBOUND
                        and _to_iso(m.get("created_at")) > cursor
                    ]

            messages = []
            for m in msg_rows:
                outbound = m.get("direction") == OUTBOUND
                msg_time = _to_iso(m.get("created_at"))
                attachments_raw = m.get("attachments")
                messages.append({
                    "message_id": m.get("message_id", ""),
                    "sender_id": m.get("sender_id", ""),
                    # Counterpart (inbound) names are stored at write time. The
                    # agent's OWN (outbound) reply is written with an empty
                    # sender_name, so reuse the display name already resolved
                    # for the member strip — otherwise this falls through to
                    # sender_id and the card shows the raw `agent_<hex>` id.
                    "sender_name": (
                        agent_display if outbound
                        else (m.get("sender_name") or m.get("sender_id", ""))
                    ),
                    "content": m.get("content", ""),
                    "attachments": json.loads(attachments_raw) if attachments_raw else None,
                    "is_read": outbound or msg_time <= cursor,
                    "created_at": msg_time,
                })

            rooms.append({
                "room_id": thread_id,
                "room_name": thread.get("title") or thread_id,
                "members": [
                    {"agent_id": agent_id, "agent_name": agent_display},
                    {
                        "agent_id": thread.get("counterpart_id", ""),
                        "agent_name": thread.get("counterpart_name")
                        or thread.get("counterpart_id", ""),
                    },
                ],
                "unread_count": unread,
                "messages": messages,
                "latest_at": _to_iso(thread.get("last_message_at")),
            })

        # Unread first, then most recent activity.
        rooms.sort(key=lambda r: (r["unread_count"] == 0, r.get("latest_at") or ""), reverse=True)

        return {"success": True, "rooms": rooms, "total_unread": total_unread}

    except Exception as e:
        logger.exception(f"[get_agent_inbox] Error: {e}", exc_info=True)
        return {"success": False, "rooms": [], "total_unread": 0, "error": "Failed to load inbox."}


@router.put("/{message_id}/read")
async def mark_message_read(message_id: str, request: Request, agent_id: str = Query(...)):
    """Advance the thread's read cursor to this message's timestamp. Owner-only.

    Clears messages up to and including ``message_id`` only. For "clear the
    whole thread" use ``POST /rooms/{room_id}/read``, which advances to NOW
    without needing a message id.
    """
    await assert_owned(request, agent_id)
    try:
        db = await _get_db()

        msg = await db.get_one("inbox_thread_messages", {"message_id": message_id})
        if not msg:
            return {"success": False, "error": "Message not found", "marked_count": 0}

        thread_id = msg["thread_id"]
        msg_time = _to_iso(msg.get("created_at"))

        # Ownership is per thread, not per message: a message id alone would
        # let a caller advance a cursor in somebody else's thread.
        thread = await db.get_one(
            "inbox_threads", {"thread_id": thread_id, "agent_id": agent_id}
        )
        if not thread:
            return {
                "success": False,
                "error": f"agent {agent_id} has no thread {thread_id}",
                "marked_count": 0,
            }

        await db.execute(
            "UPDATE inbox_threads SET last_read_at = %s "
            "WHERE thread_id = %s AND (last_read_at IS NULL OR last_read_at < %s)",
            (msg_time, thread_id, msg_time),
            fetch=False,
        )
        return {"success": True, "marked_count": 1}

    except Exception as e:
        logger.exception(f"[mark_message_read] Error: {e}", exc_info=True)
        return {"success": False, "error": "Failed to mark message read.", "marked_count": 0}


@router.post("/rooms/{room_id}/read")
async def mark_room_read(room_id: str, request: Request, agent_id: str = Query(...)):
    """Mark EVERY message in a thread read by advancing the cursor to NOW.

    The list caps each thread's ``messages`` array while ``unread_count`` is
    computed against all of them, so advancing to the latest VISIBLE message
    would leave an older-unread tail behind. Advancing to NOW guarantees zero
    residual unread.

    Idempotent — the only-advances guard makes a re-click a no-op.
    """
    await assert_owned(request, agent_id)
    try:
        db = await _get_db()

        # Verify the thread is this agent's before updating: a silent
        # zero-row UPDATE would let the caller believe the click worked.
        thread = await db.get_one(
            "inbox_threads", {"thread_id": room_id, "agent_id": agent_id}
        )
        if not thread:
            return {
                "success": False,
                "error": f"agent {agent_id} has no thread {room_id}",
                "marked_count": 0,
            }

        # Offset-FREE naive UTC. `last_read_at` is a DATETIME(6) column on
        # MySQL, where an offset-bearing literal (`…+00:00`) is shifted by the
        # session `time_zone` while a naive one is not. `created_at` and
        # `mark_message_read`'s cursor read back naive on MySQL, so an offset
        # room cursor would land on a different wall clock under any non-UTC
        # session — new messages then read as permanently unread, or the
        # only-advances guard turns every click into a silent no-op. On SQLite
        # the column is TEXT but every `*_at` read is re-normalised to UTC-aware
        # (`_auto_parse_row`), so the two cursors compare consistently there
        # regardless of what was written; this fix is for MySQL.
        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        await db.execute(
            "UPDATE inbox_threads SET last_read_at = %s "
            "WHERE thread_id = %s AND (last_read_at IS NULL OR last_read_at < %s)",
            (now_iso, room_id, now_iso),
            fetch=False,
        )
        return {"success": True, "channel_id": room_id, "last_read_at": now_iso}

    except Exception as e:
        logger.exception(f"[mark_room_read] Error: {e}", exc_info=True)
        return {"success": False, "error": "Failed to mark room read.", "marked_count": 0}


@router.get("/attachments/raw")
async def get_bus_attachment_raw(request: Request, path: str = Query(...)):
    """Stream an inbox attachment from the per-user shared area.

    ``path`` is the ``rel_path`` from a message's ``attachments`` entry (as
    returned by the inbox / team-chat APIs). Access is gated to the
    authenticated user's own root — see ``resolve_shared_file_for_user`` — so a
    tampered path can only ever reach files the caller already owns.
    """
    from backend.auth import resolve_current_user_id
    from narranexus.platform.message_bus.attachments import (
        resolve_shared_file_for_user,
    )

    user_id = await resolve_current_user_id(request)
    resolved = resolve_shared_file_for_user(user_id, path)
    if resolved is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Attachment not found"},
        )
    mime, _ = mimetypes.guess_type(str(resolved))
    return FileResponse(
        path=str(resolved),
        media_type=mime or "application/octet-stream",
        filename=Path(resolved).name,
    )
