"""
@file_name: test_pending_channel_scope.py
@author: Bin Liang
@date: 2026-08-23
@description: get_pending_messages(channel_id=...) scopes the LIMIT to ONE
room. The per-lane trigger needs this: an agent with a backlog in a busy room
A must still see a new message in room B. Without SQL scoping the cross-channel
LIMIT fills with A's rows and B is filtered to empty in Python → B starves and
burns a worker slot every poll. Delete the `AND m.channel_id` clause and the
scoped assertion below goes red.
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus


ROOM_A = "ch_busy_a"
ROOM_B = "ch_quiet_b"
ME = "agent_scope_me"


async def _room(db, channel_id):
    await db.insert("bus_channels", {
        "channel_id": channel_id, "name": "r", "channel_type": "group",
        "created_by": "team_t1",
    })
    await db.insert("bus_channel_members", {"channel_id": channel_id, "agent_id": ME})


@pytest.mark.asyncio
async def test_channel_scope_lets_a_busy_agents_other_room_be_seen(db_client):
    bus = LocalMessageBus(backend=db_client._backend)
    await _room(db_client, ROOM_A)
    await _room(db_client, ROOM_B)

    # Room A backlog (older) exceeds the tiny limit; room B has one newer msg.
    for i in range(3):
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM_A,
                               content=f"a{i}", mentions=[ME])
    await bus.send_message(from_agent="usr_u1", to_channel=ROOM_B,
                           content="b0", mentions=[ME])

    # Unscoped, limit 2: the oldest two (both room A) — room B is invisible,
    # exactly the starvation the scoping fixes.
    unscoped = await bus.get_pending_messages(ME, limit=2)
    assert {m.channel_id for m in unscoped} == {ROOM_A}

    # Scoped to room B: its message is returned despite A's backlog.
    scoped = await bus.get_pending_messages(ME, limit=2, channel_id=ROOM_B)
    assert [m.content for m in scoped] == ["b0"]

    # Scoped to room A: only A's, LIMIT lands on A alone.
    scoped_a = await bus.get_pending_messages(ME, limit=2, channel_id=ROOM_A)
    assert {m.channel_id for m in scoped_a} == {ROOM_A}
    assert len(scoped_a) == 2


@pytest.mark.asyncio
async def test_channel_none_keeps_the_cross_channel_query(db_client):
    # The default (poller / any legacy caller) is unchanged: no channel clause.
    bus = LocalMessageBus(backend=db_client._backend)
    await _room(db_client, ROOM_A)
    await _room(db_client, ROOM_B)
    await bus.send_message(from_agent="usr_u1", to_channel=ROOM_A, content="a", mentions=[ME])
    await bus.send_message(from_agent="usr_u1", to_channel=ROOM_B, content="b", mentions=[ME])

    both = await bus.get_pending_messages(ME)
    assert {m.channel_id for m in both} == {ROOM_A, ROOM_B}
