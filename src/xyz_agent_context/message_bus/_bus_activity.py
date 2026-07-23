"""
@file_name: _bus_activity.py
@author: NarraNexus
@date: 2026-07-22
@description: Lightweight live-activity status for team-room agent runs.

MessageBusTrigger runs a team-room agent in the background — the team chat UI
has no live stream to it (unlike the WS single-agent path). This module mirrors
"what is this agent doing right now" into the ``bus_agent_activity`` table so the
team-chat GET can show running / phase / elapsed. One row per
(agent_id, channel_id); ``updated_at`` is a heartbeat.

Deliberately NOT the ``events`` pipeline — this is a cheap status mirror the
trigger updates around/inside a run; it never affects delivery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

# A 'running' row whose heartbeat is older than this is treated as dead (the
# trigger process died mid-run) — readers show it as not-running.
ACTIVITY_STALE_SECONDS = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _upsert(db, agent_id: str, channel_id: str, fields: dict) -> None:
    """Update the (agent_id, channel_id) row, inserting it if absent."""
    filt = {"agent_id": agent_id, "channel_id": channel_id}
    updated = await db.update("bus_agent_activity", filt, fields)
    if not updated:
        await db.insert("bus_agent_activity", {**filt, **fields})


async def mark_running(db, agent_id: str, channel_id: str, phase: str = "starting") -> None:
    now = _now_iso()
    await _upsert(db, agent_id, channel_id, {
        "state": "running", "phase": phase, "tool_count": 0,
        "started_at": now, "updated_at": now,
    })


async def update_phase(db, agent_id: str, channel_id: str, phase: str, tool_count: int) -> None:
    await _upsert(db, agent_id, channel_id, {
        "state": "running", "phase": phase, "tool_count": int(tool_count),
        "updated_at": _now_iso(),
    })


async def mark_idle(db, agent_id: str, channel_id: str) -> None:
    await _upsert(db, agent_id, channel_id, {"state": "idle", "phase": None, "updated_at": _now_iso()})


def is_live(row: Any) -> bool:
    """True if the row is a fresh 'running' heartbeat (not stale/dead)."""
    if not row or row.get("state") != "running":
        return False
    ts = row.get("updated_at")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() < ACTIVITY_STALE_SECONDS


async def get_channel_activity(db, channel_id: str) -> List[dict]:
    """All activity rows for a channel (for the team-chat status view)."""
    return await db.get("bus_agent_activity", {"channel_id": channel_id})
