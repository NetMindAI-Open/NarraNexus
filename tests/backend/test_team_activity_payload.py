"""
@file_name: test_team_activity_payload.py
@date: 2026-07-28
@description: ``teams.py::_member_activity`` — the four-state payload behind
the team activity console, and its timestamps.

Two regressions are pinned here:

* `stalled` used to be indistinguishable from `queued`. A turn that started and
  then went quiet read as "queued", so a wedged worker looked exactly like a
  busy room and nobody went looking.
* every timestamp on this route was serialised WITHOUT a timezone marker
  (`_to_iso` was a bare `.isoformat()`), so the browser parsed stored UTC as
  local time — group-chat timestamps ran an hour early for a UTC+1 user while
  the 1:1 route, which goes through `format_for_api`, was correct.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.routes.teams import _member_activity
from xyz_agent_context.message_bus import _bus_activity as act
from xyz_agent_context.message_bus.local_bus import LocalMessageBus

ROOM = "ch_team"
MEMBERS = ["agent_a", "agent_b", "agent_c"]


async def _room(db_client) -> LocalMessageBus:
    await db_client.insert("bus_channels", {
        "channel_id": ROOM, "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    for aid in MEMBERS:
        await db_client.insert("bus_channel_members", {
            "channel_id": ROOM, "agent_id": aid,
        })
    return LocalMessageBus(db_client._backend)


async def _write_activity(db, agent_id, *, state, updated_at, started_at=None, steps=None,
                           event_id=None):
    await db.insert("bus_agent_activity", {
        "agent_id": agent_id,
        "channel_id": ROOM,
        "state": state,
        "phase": "tool:Read",
        "tool_count": 3,
        "steps": steps,
        "started_at": (started_at or updated_at),
        "updated_at": updated_at,
        "event_id": event_id,
    })


def _by_id(rows):
    return {r["agent_id"]: r for r in rows}


@pytest.mark.asyncio
async def test_idle_when_nothing_is_happening(db_client):
    bus = await _room(db_client)
    rows = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))
    assert {r["status"] for r in rows.values()} == {"idle"}
    # An idle member with no history carries no timing noise.
    assert "steps" not in rows["agent_a"]


@pytest.mark.asyncio
async def test_running_carries_phase_steps_and_signal_time(db_client):
    bus = await _room(db_client)
    now = datetime.now(timezone.utc)
    await _write_activity(
        db_client, "agent_a", state="running", updated_at=now.isoformat(),
        steps='{"items": [{"phase": "starting", "at": "x"}], "dropped": 0}',
    )

    row = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))["agent_a"]
    assert row["status"] == "running"
    assert row["phase"] == "tool:Read" and row["tool_count"] == 3
    assert row["steps"]["items"][0]["phase"] == "starting"
    assert row["last_signal_at"] is not None


@pytest.mark.asyncio
async def test_stalled_is_not_reported_as_queued(db_client):
    """The regression: a started-then-silent turn must say so."""
    bus = await _room(db_client)
    dead = datetime.now(timezone.utc) - timedelta(seconds=act.ACTIVITY_STALE_SECONDS + 30)
    await _write_activity(db_client, "agent_a", state="running", updated_at=dead.isoformat())

    row = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))["agent_a"]
    assert row["status"] == "stalled"
    assert row["last_signal_at"] is not None  # what "silent for N" counts from


@pytest.mark.asyncio
async def test_stalled_wins_over_a_pending_mention(db_client):
    """An agent can be both mid-turn-and-silent AND have a newer @mention
    waiting. The in-flight turn is the more urgent truth."""
    bus = await _room(db_client)
    dead = datetime.now(timezone.utc) - timedelta(seconds=act.ACTIVITY_STALE_SECONDS + 30)
    await _write_activity(db_client, "agent_a", state="running", updated_at=dead.isoformat())
    await bus.send_message(
        from_agent="usr_u1", to_channel=ROOM, content="hi", mentions=["agent_a"],
    )

    row = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))["agent_a"]
    assert row["status"] == "stalled"


@pytest.mark.asyncio
async def test_queued_reports_how_many_and_since_when(db_client):
    bus = await _room(db_client)
    await bus.send_message(
        from_agent="usr_u1", to_channel=ROOM, content="one", mentions=["agent_a"],
    )
    await bus.send_message(
        from_agent="usr_u1", to_channel=ROOM, content="two", mentions=["agent_a"],
    )

    rows = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))
    assert rows["agent_a"]["status"] == "queued"
    assert rows["agent_a"]["queued_count"] == 2
    assert rows["agent_a"]["queued_since"].endswith("Z")
    assert rows["agent_b"]["status"] == "idle"


@pytest.mark.asyncio
async def test_idle_keeps_the_previous_turn_trace(db_client):
    bus = await _room(db_client)
    now = datetime.now(timezone.utc)
    await _write_activity(
        db_client, "agent_a", state="idle", updated_at=now.isoformat(),
        steps='{"items": [{"phase": "replying", "at": "x"}], "dropped": 0}',
    )

    row = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))["agent_a"]
    assert row["status"] == "idle"
    assert row["finished_at"] is not None
    assert row["steps"]["items"][0]["phase"] == "replying"


@pytest.mark.asyncio
async def test_every_timestamp_is_utc_marked(db_client):
    """Bare `.isoformat()` output parses as LOCAL time in the browser — the
    one-hour-early group chat timestamps. Every field must carry the Z."""
    bus = await _room(db_client)
    now = datetime.now(timezone.utc)
    await _write_activity(db_client, "agent_a", state="running", updated_at=now.isoformat())
    await _write_activity(db_client, "agent_b", state="idle", updated_at=now.isoformat(),
                          steps='{"items": [{"phase": "replying", "at": "x"}], "dropped": 0}')
    await bus.send_message(
        from_agent="usr_u1", to_channel=ROOM, content="hi", mentions=["agent_c"],
    )

    rows = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))
    stamps = [
        rows["agent_a"]["started_at"],
        rows["agent_a"]["last_signal_at"],
        rows["agent_b"]["finished_at"],
        rows["agent_c"]["queued_since"],
    ]
    assert all(s and s.endswith("Z") for s in stamps), stamps


@pytest.mark.asyncio
async def test_a_naive_stored_timestamp_still_serialises_as_utc(db_client):
    """MySQL returns naive datetimes; they are UTC and must be marked as such."""
    bus = await _room(db_client)
    naive = datetime.now(timezone.utc).replace(tzinfo=None)
    await _write_activity(db_client, "agent_a", state="running", updated_at=naive)

    row = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))["agent_a"]
    assert row["status"] == "running"
    assert row["started_at"].endswith("Z")


@pytest.mark.asyncio
async def test_a_broken_pending_read_degrades_to_idle_not_a_500(db_client, monkeypatch):
    """The activity block is an indicator, never a reason to fail the GET."""
    bus = await _room(db_client)

    async def boom(*_a, **_k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(bus, "get_room_pending_summary", boom)
    rows = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))
    assert {r["status"] for r in rows.values()} == {"idle"}


@pytest.mark.asyncio
async def test_activity_covers_every_member_in_order(db_client):
    bus = await _room(db_client)
    rows = await _member_activity(db_client, bus, ROOM, MEMBERS)
    assert [r["agent_id"] for r in rows] == MEMBERS


@pytest.mark.asyncio
async def test_idle_carries_started_at_for_duration(db_client):
    """The roster's "ran Ns" derives from started_at→finished_at. The idle
    branch used to omit started_at (only running/stalled carried it), so every
    finished turn rendered as a confident "ran 0s" while the DB held the real value.
    """
    bus = await _room(db_client)
    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=45)
    await _write_activity(
        db_client, "agent_a", state="idle", updated_at=now.isoformat(),
        started_at=start.isoformat(),
        steps='{"items": [{"phase": "replying", "at": "x"}], "dropped": 0}',
    )

    row = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))["agent_a"]
    assert row["status"] == "idle"
    assert row["started_at"] is not None and row["started_at"].endswith("Z")
    started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
    finished = datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00"))
    assert 40 <= (finished - started).total_seconds() <= 50


@pytest.mark.asyncio
async def test_payload_carries_event_id_when_present(db_client):
    """The activity payload surfaces the turn's event_id so the frontend can
    fetch the finished turn's full event_log via the existing endpoint."""
    bus = await _room(db_client)
    now = datetime.now(timezone.utc)
    await _write_activity(
        db_client, "agent_a", state="idle", updated_at=now.isoformat(),
        steps='{"items": [{"phase": "replying", "at": "x"}], "dropped": 0}',
        event_id="evt_abc",
    )
    await _write_activity(
        db_client, "agent_b", state="running", updated_at=now.isoformat(),
        event_id="evt_def",
    )

    rows = _by_id(await _member_activity(db_client, bus, ROOM, MEMBERS))
    assert rows["agent_a"]["event_id"] == "evt_abc"
    assert rows["agent_b"]["event_id"] == "evt_def"


@pytest.mark.asyncio
async def test_note_event_id_persists_and_start_resets(db_client):
    """note_event_id lands on the activity row; a new turn clears it."""
    from xyz_agent_context.message_bus import _bus_activity

    act = _bus_activity.TurnActivity(db_client, "agent_x", "chan_1")
    await act.start()
    await act.note_event_id("evt_abc123")
    row = await db_client.get_one(
        "bus_agent_activity", {"agent_id": "agent_x", "channel_id": "chan_1"}
    )
    assert row["event_id"] == "evt_abc123"

    await act.finish()
    row = await db_client.get_one(
        "bus_agent_activity", {"agent_id": "agent_x", "channel_id": "chan_1"}
    )
    assert row["event_id"] == "evt_abc123"  # kept after the turn ends

    act2 = _bus_activity.TurnActivity(db_client, "agent_x", "chan_1")
    await act2.start()
    row = await db_client.get_one(
        "bus_agent_activity", {"agent_id": "agent_x", "channel_id": "chan_1"}
    )
    assert row["event_id"] is None  # stale id must not survive into a new turn
