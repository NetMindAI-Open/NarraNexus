"""
@file_name: test_bus_activity.py
@date: 2026-07-22
@description: Team-room agent activity mirror — running/idle lifecycle, phase
updates, and heartbeat staleness (a dead 'running' row reads as not-live).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xyz_agent_context.message_bus import _bus_activity as act


@pytest.mark.asyncio
async def test_running_phase_idle_lifecycle(db_client):
    await act.mark_running(db_client, "agent_a", "ch_1")
    row = await db_client.get_one("bus_agent_activity", {"agent_id": "agent_a", "channel_id": "ch_1"})
    assert row["state"] == "running" and row["phase"] == "starting"
    assert act.is_live(row) is True

    await act.update_phase(db_client, "agent_a", "ch_1", "tool:Read", 2)
    row = await db_client.get_one("bus_agent_activity", {"agent_id": "agent_a", "channel_id": "ch_1"})
    assert row["phase"] == "tool:Read" and row["tool_count"] == 2 and act.is_live(row)

    await act.mark_idle(db_client, "agent_a", "ch_1")
    row = await db_client.get_one("bus_agent_activity", {"agent_id": "agent_a", "channel_id": "ch_1"})
    assert row["state"] == "idle" and act.is_live(row) is False


def test_is_live_staleness():
    now = datetime.now(timezone.utc)
    assert act.is_live({"state": "running", "updated_at": now.isoformat()}) is True
    stale = (now - timedelta(seconds=act.ACTIVITY_STALE_SECONDS + 5)).isoformat()
    assert act.is_live({"state": "running", "updated_at": stale}) is False  # heartbeat dead
    assert act.is_live({"state": "idle", "updated_at": now.isoformat()}) is False
    assert act.is_live(None) is False


@pytest.mark.asyncio
async def test_get_channel_activity_scopes_by_channel(db_client):
    await act.mark_running(db_client, "agent_a", "ch_1")
    await act.mark_running(db_client, "agent_b", "ch_1")
    await act.mark_running(db_client, "agent_c", "ch_other")
    rows = await act.get_channel_activity(db_client, "ch_1")
    assert {r["agent_id"] for r in rows} == {"agent_a", "agent_b"}
