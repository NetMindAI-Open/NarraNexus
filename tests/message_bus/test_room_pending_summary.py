"""
@file_name: test_room_pending_summary.py
@date: 2026-07-28
@description: ``LocalMessageBus.get_room_pending_summary`` — the batched
"what is still waiting for you in this room" read behind the team-chat
`queued` status.

It has to agree with ``get_pending_messages`` on what pending MEANS (cursor,
self-sent, poison) while answering for every member at once: the team chat
polls this every few seconds, and the per-member version cost one query per
member plus one failure lookup per pending row.
"""

from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import (
    POISON_FAILURE_THRESHOLD,
    LocalMessageBus,
    _as_utc,
)

ROOM = "ch_team"
MEMBERS = ["agent_a", "agent_b", "agent_c"]


async def _make_room(db_client, channel_id: str, members: list[str]) -> None:
    """Build a team room the way ``teams.py::_get_or_create_team_room`` does:
    a group channel whose ``created_by`` is the synthetic ``team_<id>`` marker,
    so no member agent is the always-activated channel owner."""
    await db_client.insert("bus_channels", {
        "channel_id": channel_id,
        "name": f"room {channel_id}",
        "channel_type": "group",
        "created_by": "team_t1",
    })
    for aid in members:
        await db_client.insert("bus_channel_members", {
            "channel_id": channel_id, "agent_id": aid,
        })


async def _room(db_client) -> LocalMessageBus:
    await _make_room(db_client, ROOM, MEMBERS)
    return LocalMessageBus(db_client._backend)


async def _say(bus, sender: str, text: str, mentions=None) -> str:
    return await bus.send_message(
        from_agent=sender, to_channel=ROOM, content=text, mentions=mentions
    )


@pytest.mark.asyncio
async def test_only_addressed_members_are_pending(db_client):
    bus = await _room(db_client)
    await _say(bus, "usr_u1", "hello @a", mentions=["agent_a"])

    summary = await bus.get_room_pending_summary(ROOM, MEMBERS)
    assert set(summary) == {"agent_a"}
    assert summary["agent_a"]["count"] == 1


@pytest.mark.asyncio
async def test_unaddressed_messages_are_not_pending_for_anyone(db_client):
    """An agent reply with no @mention triggers nobody; it must not light up
    the whole room as queued."""
    bus = await _room(db_client)
    await _say(bus, "agent_a", "just thinking out loud")

    assert await bus.get_room_pending_summary(ROOM, MEMBERS) == {}


@pytest.mark.asyncio
async def test_everyone_addresses_all_but_the_sender(db_client):
    bus = await _room(db_client)
    await _say(bus, "agent_a", "all hands", mentions=["@everyone"])

    summary = await bus.get_room_pending_summary(ROOM, MEMBERS)
    assert set(summary) == {"agent_b", "agent_c"}  # never yourself


@pytest.mark.asyncio
async def test_cursor_clears_the_backlog(db_client):
    bus = await _room(db_client)
    await _say(bus, "usr_u1", "first", mentions=["agent_a"])
    second = await _say(bus, "usr_u1", "second", mentions=["agent_a"])

    summary = await bus.get_room_pending_summary(ROOM, MEMBERS)
    assert summary["agent_a"]["count"] == 2
    oldest_before = summary["agent_a"]["oldest_at"]

    msg = (await bus.get_messages(ROOM, limit=10))[-1]
    assert msg.message_id == second
    await bus.ack_processed("agent_a", ROOM, msg.created_at)

    assert await bus.get_room_pending_summary(ROOM, MEMBERS) == {}
    assert oldest_before is not None


@pytest.mark.asyncio
async def test_a_partial_cursor_leaves_only_the_newer_messages(db_client):
    bus = await _room(db_client)
    first = await _say(bus, "usr_u1", "first", mentions=["agent_a"])
    await _say(bus, "usr_u1", "second", mentions=["agent_a"])

    msgs = {m.message_id: m for m in await bus.get_messages(ROOM, limit=10)}
    await bus.ack_processed("agent_a", ROOM, msgs[first].created_at)

    summary = await bus.get_room_pending_summary(ROOM, MEMBERS)
    assert summary["agent_a"]["count"] == 1


@pytest.mark.asyncio
async def test_members_keep_independent_cursors(db_client):
    bus = await _room(db_client)
    await _say(bus, "usr_u1", "all hands", mentions=["@everyone"])

    msg = (await bus.get_messages(ROOM, limit=10))[-1]
    await bus.ack_processed("agent_b", ROOM, msg.created_at)

    summary = await bus.get_room_pending_summary(ROOM, MEMBERS)
    # The user addressed everyone, so all three were pending; only b caught up.
    assert set(summary) == {"agent_a", "agent_c"}


@pytest.mark.asyncio
async def test_poisoned_messages_stop_counting_as_queued(db_client):
    """A message past the poison threshold will never run again — showing it
    as "waiting to start" would be a lie that never resolves."""
    bus = await _room(db_client)
    msg_id = await _say(bus, "usr_u1", "cursed", mentions=["agent_a"])
    for _ in range(POISON_FAILURE_THRESHOLD):
        await bus.record_failure(msg_id, "agent_a", "boom")

    assert await bus.get_room_pending_summary(ROOM, MEMBERS) == {}


@pytest.mark.asyncio
async def test_failures_below_the_threshold_still_count(db_client):
    bus = await _room(db_client)
    msg_id = await _say(bus, "usr_u1", "retry me", mentions=["agent_a"])
    await bus.record_failure(msg_id, "agent_a", "transient")

    summary = await bus.get_room_pending_summary(ROOM, MEMBERS)
    assert summary["agent_a"]["count"] == 1


@pytest.mark.asyncio
async def test_a_poison_row_for_another_agent_does_not_leak(db_client):
    bus = await _room(db_client)
    msg_id = await _say(bus, "usr_u1", "all hands", mentions=["@everyone"])
    for _ in range(POISON_FAILURE_THRESHOLD):
        await bus.record_failure(msg_id, "agent_b", "boom")

    summary = await bus.get_room_pending_summary(ROOM, MEMBERS)
    assert set(summary) == {"agent_a", "agent_c"}  # b poisoned, the rest untouched


@pytest.mark.asyncio
async def test_oldest_at_is_the_first_unprocessed_mention(db_client):
    bus = await _room(db_client)
    first = await _say(bus, "usr_u1", "first", mentions=["agent_a"])
    await _say(bus, "usr_u1", "second", mentions=["agent_a"])

    msgs = {m.message_id: m for m in await bus.get_messages(ROOM, limit=10)}
    summary = await bus.get_room_pending_summary(ROOM, MEMBERS)

    assert summary["agent_a"]["count"] == 2
    # `oldest_at` is normalised to an aware UTC datetime; the stored value may
    # arrive as a naive datetime (MySQL) or an ISO string (SQLite).
    assert summary["agent_a"]["oldest_at"] == _as_utc(msgs[first].created_at)


@pytest.mark.asyncio
async def test_other_rooms_do_not_bleed_in(db_client):
    bus = await _room(db_client)
    await _make_room(db_client, "ch_other", ["agent_a"])
    await bus.send_message(
        from_agent="usr_u1", to_channel="ch_other", content="elsewhere",
        mentions=["agent_a"],
    )

    assert await bus.get_room_pending_summary(ROOM, MEMBERS) == {}


@pytest.mark.asyncio
async def test_empty_member_list_is_a_no_op(db_client):
    bus = await _room(db_client)
    await _say(bus, "usr_u1", "hello", mentions=["agent_a"])
    assert await bus.get_room_pending_summary(ROOM, []) == {}


@pytest.mark.asyncio
async def test_non_members_are_ignored(db_client):
    bus = await _room(db_client)
    await _say(bus, "usr_u1", "hello", mentions=["agent_stranger"])
    assert await bus.get_room_pending_summary(ROOM, MEMBERS) == {}
