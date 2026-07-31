"""
@file_name: test_bus_activity.py
@date: 2026-07-22
@description: Team-room agent activity mirror — turn lifecycle, the step
timeline, the timer heartbeat, and the running/stalled/idle taxonomy.

The heartbeat tests are the point of this file: before it existed the only
writer was the runtime's progress callback, so a silent stretch (a long tool, a
model thinking for minutes) let `updated_at` go stale and the UI downgraded a
perfectly healthy turn to "queued".
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from xyz_agent_context.message_bus import _bus_activity as act


async def _row(db, agent_id="agent_a", channel_id="ch_1"):
    return await db.get_one(
        "bus_agent_activity", {"agent_id": agent_id, "channel_id": channel_id}
    )


@pytest.mark.asyncio
async def test_turn_lifecycle_records_steps_and_goes_idle(db_client):
    async with act.turn(db_client, "agent_a", "ch_1") as turn:
        row = await _row(db_client)
        assert row["state"] == "running" and row["phase"] == "starting"
        assert act.is_live(row) is True
        assert [s["phase"] for s in act.parse_steps(row)["items"]] == ["starting"]

        await turn.on_progress("thinking")
        await turn.on_progress("tool", "Read")
        await turn.on_progress("tool", "Read")  # same phase → no new step
        await turn.on_progress("response")

        row = await _row(db_client)
        assert row["phase"] == "replying"
        # Two Read frames both count as tool uses even though only one step.
        assert row["tool_count"] == 2
        phases = [s["phase"] for s in act.parse_steps(row)["items"]]
        assert phases == ["starting", "thinking", "tool:Read", "replying"]

    row = await _row(db_client)
    assert row["state"] == "idle"
    assert act.is_live(row) is False
    # The finished turn's trace survives so the room can show what it just did.
    assert [s["phase"] for s in act.parse_steps(row)["items"]][-1] == "replying"


@pytest.mark.asyncio
async def test_turn_goes_idle_even_when_the_body_raises(db_client):
    with pytest.raises(RuntimeError):
        async with act.turn(db_client, "agent_a", "ch_1"):
            raise RuntimeError("run blew up")
    row = await _row(db_client)
    assert row["state"] == "idle"


@pytest.mark.asyncio
async def test_heartbeat_refreshes_without_any_progress(db_client, monkeypatch):
    """A turn that emits nothing must still look alive.

    This is the regression the whole feature exists for: no on_progress call
    happens here at all, and the row must still be fresh afterwards.
    """
    monkeypatch.setattr(act, "HEARTBEAT_INTERVAL_SECONDS", 0.05)

    async with act.turn(db_client, "agent_a", "ch_1"):
        first = str((await _row(db_client))["updated_at"])
        await asyncio.sleep(0.25)
        beat = str((await _row(db_client))["updated_at"])

    assert beat > first, "timer heartbeat never refreshed updated_at"


@pytest.mark.asyncio
async def test_heartbeat_survives_a_write_failure(db_client, monkeypatch):
    """A DB blip must cost one beat, not the run."""
    monkeypatch.setattr(act, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    calls = {"n": 0}
    real_upsert = act._upsert

    async def flaky(db, agent_id, channel_id, fields):
        calls["n"] += 1
        if calls["n"] == 2:  # the first heartbeat, right after start()
            raise RuntimeError("connection reset")
        return await real_upsert(db, agent_id, channel_id, fields)

    monkeypatch.setattr(act, "_upsert", flaky)

    async with act.turn(db_client, "agent_a", "ch_1"):
        await asyncio.sleep(0.25)

    assert calls["n"] >= 3, "heartbeat stopped after the failed write"
    assert (await _row(db_client))["state"] == "idle"


def test_status_taxonomy_separates_stalled_from_not_running():
    now = datetime.now(timezone.utc)
    fresh = {"state": "running", "updated_at": now.isoformat()}
    stale = {
        "state": "running",
        "updated_at": (now - timedelta(seconds=act.ACTIVITY_STALE_SECONDS + 5)).isoformat(),
    }
    idle = {"state": "idle", "updated_at": now.isoformat()}

    assert (act.is_live(fresh), act.is_stalled(fresh)) == (True, False)
    # Started, then went quiet — NOT the same thing as "never started".
    assert (act.is_live(stale), act.is_stalled(stale)) == (False, True)
    assert (act.is_live(idle), act.is_stalled(idle)) == (False, False)
    assert (act.is_live(None), act.is_stalled(None)) == (False, False)
    # A running row with no heartbeat at all is stalled, not live.
    assert act.is_stalled({"state": "running", "updated_at": None}) is True


def test_is_live_accepts_naive_timestamps_as_utc():
    """MySQL hands back naive datetimes; reading them as local time would make
    every row look hours stale in any non-UTC deployment."""
    naive = datetime.now(timezone.utc).replace(tzinfo=None)
    assert act.is_live({"state": "running", "updated_at": naive}) is True


@pytest.mark.asyncio
async def test_steps_are_capped_and_the_drop_is_counted(db_client):
    async with act.turn(db_client, "agent_a", "ch_1") as turn:
        for i in range(act.MAX_STEPS + 6):
            await turn.on_progress("tool", f"Tool{i}")
        parsed = act.parse_steps(await _row(db_client))

    assert len(parsed["items"]) == act.MAX_STEPS
    assert parsed["dropped"] == 7  # 'starting' + 6 tools past the cap
    # The cap drops from the FRONT: the newest steps are the ones kept.
    assert parsed["items"][-1]["phase"] == f"tool:Tool{act.MAX_STEPS + 5}"


@pytest.mark.parametrize("raw", [None, "", "not json", "[]", '{"items": "nope"}', 123])
def test_parse_steps_never_raises_on_junk(raw):
    assert act.parse_steps({"steps": raw}) == {"items": [], "dropped": 0}


def test_parse_steps_handles_a_missing_row():
    assert act.parse_steps(None) == {"items": [], "dropped": 0}


def test_parse_steps_accepts_an_already_decoded_blob():
    blob = {"items": [{"phase": "thinking", "at": "x"}], "dropped": 2}
    assert act.parse_steps({"steps": blob}) == blob
    assert act.parse_steps({"steps": json.dumps(blob)}) == blob


@pytest.mark.asyncio
async def test_unknown_progress_kinds_are_ignored(db_client):
    async with act.turn(db_client, "agent_a", "ch_1") as turn:
        await turn.on_progress("error")
        await turn.on_progress("something-new")
        parsed = act.parse_steps(await _row(db_client))
    assert [s["phase"] for s in parsed["items"]] == ["starting"]


@pytest.mark.asyncio
async def test_get_channel_activity_scopes_by_channel(db_client):
    async with act.turn(db_client, "agent_a", "ch_1"):
        pass
    async with act.turn(db_client, "agent_b", "ch_1"):
        pass
    async with act.turn(db_client, "agent_c", "ch_other"):
        pass
    rows = await act.get_channel_activity(db_client, "ch_1")
    assert {r["agent_id"] for r in rows} == {"agent_a", "agent_b"}
