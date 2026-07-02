"""
@file_name: agents_bus_failures.py
@author: Bin Liang
@date: 2026-07-02
@description: List + retry permanently-failed MessageBus deliveries for an agent.

Endpoints:
  GET  /{agent_id}/bus-failures                        — list failed messages
  POST /{agent_id}/bus-failures/{message_id}/retry      — clear a failure record

Upstream report: NetMindAI-Open/NarraNexus#52 — a broken LLM provider (e.g. an
invalid OpenAI key) makes every MessageBus delivery attempt for an agent raise.
`LocalMessageBus.get_pending_messages` permanently filters a message out once
`bus_message_failures.retry_count` reaches 3 (see local_bus.py), and until now
that was a pure silent failure — no UI, no notification, no way back short of
a direct DB edit. `MessageBusTrigger._notify_permanent_failure`
(message_bus_trigger.py) now writes an inbox notice once a message crosses
that threshold; this route is the recovery half — once the owner has fixed
the underlying problem, they clear the failure record so the message is
picked up on the next poll cycle.

Per-viewer tenancy (same pattern as agents_cost.py): viewer_id resolved from
the session (JWT / X-User-Id), never from a query param. A failure row may
only be listed/retried by the OWNER of the target agent
(agents.created_by == viewer_id) — otherwise 404 (masks "no such agent" and
"not yours" identically, same defense-in-depth rationale as agents_cost.py).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from xyz_agent_context.utils.db_factory import get_db_client

router = APIRouter()


async def _resolve_viewer_id(request: Request) -> str:
    """Resolve viewer_id from the session — never from a query param.

    Mirrors `agents_cost.py._resolve_viewer_id` (TDR-12): a `?user_id=`
    query param is rejected outright rather than silently ignored, so a
    caller can't be confused about which identity won.
    """
    if "user_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="user_id query param not accepted; viewer identified by session",
        )
    return await resolve_current_user_id(request)


async def _require_owned_agent(db, agent_id: str, viewer_id: str) -> None:
    """Raise 404 unless `viewer_id` owns `agent_id`."""
    owner_row = await db.execute(
        "SELECT created_by FROM agents WHERE agent_id=%s LIMIT 1",
        (agent_id,),
    )
    if not owner_row or owner_row[0]["created_by"] != viewer_id:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/bus-failures")
async def list_bus_failures(request: Request, agent_id: str):
    """List permanently-failed MessageBus deliveries for an agent
    (``bus_message_failures.retry_count >= 3`` — the poison threshold
    ``get_pending_messages`` filters on, see local_bus.py)."""
    viewer_id = await _resolve_viewer_id(request)
    db = await get_db_client()
    await _require_owned_agent(db, agent_id, viewer_id)

    rows = await db.execute(
        "SELECT f.message_id, f.agent_id, f.retry_count, f.last_error, "
        "f.last_retry_at, m.channel_id, m.content, m.from_agent, "
        "m.created_at AS message_created_at "
        "FROM bus_message_failures f "
        "JOIN bus_messages m ON m.message_id = f.message_id "
        "WHERE f.agent_id = %s AND f.retry_count >= 3 "
        "ORDER BY f.last_retry_at DESC",
        (agent_id,),
    )
    return {
        "success": True,
        "failures": [
            {
                "message_id": r["message_id"],
                "channel_id": r["channel_id"],
                "from_agent": r["from_agent"],
                "content": r["content"],
                "retry_count": r["retry_count"],
                "last_error": r["last_error"],
                "last_retry_at": r["last_retry_at"],
                "message_created_at": r["message_created_at"],
            }
            for r in (rows or [])
        ],
    }


@router.post("/{agent_id}/bus-failures/{message_id}/retry")
async def retry_bus_failure(request: Request, agent_id: str, message_id: str):
    """Clear the failure record for one message so the next poll cycle
    re-delivers it.

    Safe to do without touching the delivery cursor: a failed message never
    advances ``bus_channel_members.last_processed_at`` (only the success path
    in ``_handle_channel_batch`` calls ``ack_processed`` — see
    message_bus_trigger.py), so ``get_pending_messages`` will surface the
    message again on the very next poll once ``retry_count`` drops below 3.
    """
    viewer_id = await _resolve_viewer_id(request)
    db = await get_db_client()
    await _require_owned_agent(db, agent_id, viewer_id)

    existing = await db.get_one(
        "bus_message_failures", {"message_id": message_id, "agent_id": agent_id}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Failure record not found")

    await db.delete(
        "bus_message_failures", {"message_id": message_id, "agent_id": agent_id}
    )
    logger.info(
        f"[agents_bus_failures] viewer {viewer_id} retried message "
        f"{message_id} for agent {agent_id}"
    )
    return {"success": True, "message_id": message_id, "agent_id": agent_id}
