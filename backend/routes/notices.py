"""
@file_name: notices.py
@author: Bin Liang
@date: 2026-07-03
@description: User-scope system notices — the READ side of ``inbox_table``.

``MessageBusTrigger._notify_permanent_failure`` (and future writers) drop
SYSTEM_NOTICE rows into ``inbox_table`` via ``InboxRepository`` so an owner
learns their agent permanently gave up on a message (upstream #52). Until
these routes existed the table was write-only — the notification fired and
nobody could ever see it.

Not to be confused with ``inbox.py`` (``/api/agent-inbox``): that reads the
message-bus channel tables per AGENT. This reads ``inbox_table`` per USER.

Endpoints (mounted at /api/notices):
  GET  /                       — current user's notices + unread count
  POST /{message_id}/read      — mark one notice read (404 masks foreign rows)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Query

from xyz_agent_context.repository.inbox_repository import InboxRepository
from xyz_agent_context.utils.db_factory import get_db_client

from backend.auth import resolve_current_user_id

router = APIRouter()


def _to_dict(msg) -> dict[str, Any]:
    return {
        "message_id": msg.message_id,
        "message_type": msg.message_type.value,
        "title": msg.title,
        "content": msg.content,
        "is_read": msg.is_read,
        "created_at": msg.created_at.isoformat(sep=" ") if msg.created_at else "",
        "source": msg.source.model_dump() if msg.source else None,
    }


@router.get("")
async def list_notices(
    request: Request,
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    user_id = await resolve_current_user_id(request)
    repo = InboxRepository(await get_db_client())
    notices = await repo.get_messages(
        user_id=user_id,
        is_read=False if unread_only else None,
        limit=limit,
    )
    unread = await repo.get_unread_count(user_id)
    return {
        "success": True,
        "notices": [_to_dict(m) for m in notices],
        "unread_count": unread,
    }


@router.post("/{message_id}/read")
async def mark_notice_read(request: Request, message_id: str) -> dict[str, Any]:
    user_id = await resolve_current_user_id(request)
    repo = InboxRepository(await get_db_client())
    msg = await repo.get_message(message_id)
    if not msg or msg.user_id != user_id:
        # Same policy as agents_bus_failures: 404 for both "not found" and
        # "not yours" so existence of other users' notices isn't leaked.
        raise HTTPException(status_code=404, detail="Notice not found")
    await repo.mark_as_read(message_id)
    return {"success": True, "message_id": message_id}
