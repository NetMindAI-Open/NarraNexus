"""
@file_name: notifications.py
@author: Bin Liang
@date: 2026-05-13
@description: User-facing notification endpoints.

The first producer of ``user_notifications`` rows is the Provider
Unification self-heal path: when a slot.model is no longer in the
provider's models array, the resolver auto-swaps to a default and
records a ``slot_auto_repaired`` notification here so the user can
review (and possibly override) the choice the next time they open
Settings.

Endpoints
---------
* ``GET /api/notifications/me`` — list this user's notifications,
  optionally filtered to unread only, newest first
* ``POST /api/notifications/{id}/read`` — mark one notification read
* ``POST /api/notifications/read-all`` — mark every unread row read
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.timezone import utc_now


router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _row_to_dict(row: dict) -> dict:
    """Translate a raw user_notifications row into a JSON-friendly dict.

    Payload is stored as JSON text — parse it here so the frontend
    doesn't have to. Falls back to the raw string if parsing fails.
    """
    payload_raw = row.get("payload")
    payload = None
    if payload_raw:
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except (ValueError, TypeError):
            payload = {"raw": payload_raw}

    return {
        "id": row.get("id"),
        "kind": row.get("kind"),
        "severity": row.get("severity") or "info",
        "payload": payload,
        "read_at": row.get("read_at"),
        "created_at": row.get("created_at"),
    }


@router.get("/me")
async def list_my_notifications(
    request: Request,
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """List the current user's notifications.

    Returns a dict with ``items`` (newest first) and ``unread_count``
    so the bell-icon badge can be rendered without a separate request.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = await get_db_client()

    filters: dict = {"user_id": user_id}
    if unread_only:
        filters["read_at"] = None

    rows = await db.get("user_notifications", filters)
    rows = sorted(rows or [], key=lambda r: r.get("created_at") or "", reverse=True)[:limit]

    # unread_count uses a separate filtered count — cheap on the indexed
    # column and lets the UI badge stay accurate regardless of `limit`.
    unread_rows = await db.get(
        "user_notifications",
        {"user_id": user_id, "read_at": None},
    )

    return {
        "items": [_row_to_dict(r) for r in rows],
        "unread_count": len(unread_rows or []),
    }


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    request: Request,
) -> dict:
    """Mark a single notification as read.

    Only updates rows owned by the requesting user — IDs from other
    users are silently ignored (returns ok=False).
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = await get_db_client()
    row = await db.get_one(
        "user_notifications",
        {"id": notification_id, "user_id": user_id},
    )
    if not row:
        return {"ok": False, "reason": "not found or not yours"}

    if not row.get("read_at"):
        await db.update(
            "user_notifications",
            {"id": notification_id},
            {"read_at": utc_now().isoformat()},
        )
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(request: Request) -> dict:
    """Mark every unread notification of the current user as read."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = await get_db_client()
    unread = await db.get(
        "user_notifications",
        {"user_id": user_id, "read_at": None},
    )
    now_iso = utc_now().isoformat()
    updated = 0
    for row in unread or []:
        await db.update(
            "user_notifications",
            {"id": row["id"]},
            {"read_at": now_iso},
        )
        updated += 1
    return {"ok": True, "updated": updated}
