"""
@file_name: test_get_unread_contract.py
@author:
@date: 2026-08-11
@description: What `get_unread` promises — it had no contract, and it showed.

This is the query behind the `### Unread Messages` block that rides every turn's
context, in every scenario. It had three defects at once and none of them were
covered:

  * **it counted the agent's own posts.** `get_pending_messages` has excluded
    `from_agent = me` since it was written; this one never did, so an agent that
    posts in a busy room reads its own words back as somebody's unanswered
    message.
  * **it returned the OLDEST N.** `ORDER BY created_at ASC` with no SQL LIMIT,
    truncated in Python. Paired with a cursor that never advanced, every turn
    got the same frozen window of the most ancient messages — the opposite of
    "what is going on right now". `get_recent_messages` already documents the
    fix (DESC + reversed); this one just never adopted it.
  * **it had no LIMIT at all**, so the whole backlog crossed the wire to be
    thrown away in a slice.

One caller must NOT get a limit, and that is the point of the last test here:
the module's post-turn hook asks for the full set to decide which messages a
reply covers. Hand it the newest 20 and every older answered message stays
unread forever — a worse bug than the one being fixed, and a silent one.
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus


CHANNEL = "ch_room"
ME = "agent_me"
PEER = "agent_peer"


@pytest.fixture
def bus(db_client):
    return LocalMessageBus(backend=db_client._backend)


async def _room(db):
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    for aid in (ME, PEER):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})


@pytest.mark.asyncio
async def test_my_own_posts_are_not_my_unread(bus, db_client):
    """An agent reading its own words back as an open item is pure noise, and
    in a room it posts to often it drowns out everything else."""
    await _room(db_client)
    await bus.send_message(from_agent=PEER, to_channel=CHANNEL, content="from peer")
    await bus.send_message(from_agent=ME, to_channel=CHANNEL, content="from me")

    assert [m.content for m in await bus.get_unread(ME)] == ["from peer"]


@pytest.mark.asyncio
async def test_a_limit_returns_the_NEWEST_n_in_reading_order(bus, db_client):
    """Newest by selection, oldest-first by presentation.

    The window has to be the recent end — a stale window is worse than none,
    because it reads as current. But once chosen it should render in chat order,
    the way `get_recent_messages` does it.
    """
    await _room(db_client)
    for i in range(5):
        await bus.send_message(
            from_agent=PEER, to_channel=CHANNEL, content=f"m{i}"
        )

    got = [m.content for m in await bus.get_unread(ME, limit=3)]

    assert got == ["m2", "m3", "m4"]


@pytest.mark.asyncio
async def test_without_a_limit_the_whole_backlog_comes_back(bus, db_client):
    """The mark-read path depends on this. See the module hook."""
    await _room(db_client)
    for i in range(5):
        await bus.send_message(
            from_agent=PEER, to_channel=CHANNEL, content=f"m{i}"
        )

    assert len(await bus.get_unread(ME)) == 5


@pytest.mark.asyncio
async def test_the_count_is_the_backlog_not_the_window(bus, db_client):
    """The prompt renders "N unread (showing M)". Once the query is capped, N
    can no longer come from len() of the capped result."""
    await _room(db_client)
    for i in range(7):
        await bus.send_message(
            from_agent=PEER, to_channel=CHANNEL, content=f"m{i}"
        )
    await bus.send_message(from_agent=ME, to_channel=CHANNEL, content="mine")

    assert await bus.count_unread(ME) == 7


@pytest.mark.asyncio
async def test_the_read_cursor_still_bounds_the_result(bus, db_client):
    """The limit is a window onto the unread set, not a replacement for it."""
    await _room(db_client)
    await bus.send_message(from_agent=PEER, to_channel=CHANNEL, content="old")
    cutoff = (await db_client.get("bus_messages", {}))[-1]["created_at"]
    await bus.ack_read(ME, CHANNEL, cutoff)
    await bus.send_message(from_agent=PEER, to_channel=CHANNEL, content="new")

    assert [m.content for m in await bus.get_unread(ME)] == ["new"]
    assert await bus.count_unread(ME) == 1


@pytest.mark.asyncio
async def test_the_mark_read_path_must_see_beyond_the_injection_window(
    bus, db_client, monkeypatch
):
    """The reason `get_unread` keeps an uncapped mode, stated as a failure.

    The post-turn hook marks read by FILTERING the unread set down to the
    channels this turn replied in. Hand it the newest N instead of everything
    and a quiet channel can be pushed out of the window entirely by a busy one
    — so the agent replies in that channel and its cursor still never moves.
    The message it just answered stays unread forever, and the next turn is
    told to answer it again.
    """
    from types import SimpleNamespace

    from narranexus_plugins.message_bus_module import message_bus_module as mod

    await _room(db_client)
    await db_client.insert("bus_channels", {
        "channel_id": "ch_busy", "name": "busy", "channel_type": "group",
        "created_by": "team_t2",
    })
    await db_client.insert(
        "bus_channel_members", {"channel_id": "ch_busy", "agent_id": ME}
    )
    # The quiet channel's one message is the OLDEST thing in the backlog…
    await bus.send_message(from_agent=PEER, to_channel=CHANNEL, content="please help")
    # …and a busy room then buries it far past any window.
    for i in range(30):
        await bus.send_message(
            from_agent=PEER, to_channel="ch_busy", content=f"chatter {i}"
        )

    async def _bus():
        return bus

    monkeypatch.setattr(mod, "_get_default_bus_async", _bus)
    module = mod.MessageBusModule.__new__(mod.MessageBusModule)
    module.agent_id = ME

    # The frame has to look like what the trace ACTUALLY carries. It named
    # `message_team` until 2026-08-17; the hook still matched that string
    # after the tool was replaced, so nothing counted as a reply and this cursor
    # stopped advancing altogether — the deadlock this test was written to
    # prevent, re-introduced by a rename and invisible because the test carried
    # the old name too.
    frame = SimpleNamespace(
        tool_name="mcp__message_bus_module__message_agent",
        tool_input={"to": PEER, "text": "on it"},
    )
    params = SimpleNamespace(
        trace=SimpleNamespace(agent_loop_response=[frame]),
        execution_ctx=SimpleNamespace(agent_id=ME),
        io_data=None,
        ctx_data=None,
        instance=None,
    )

    await module.after_turn(params)

    row = await db_client.get_one(
        "bus_channel_members", {"channel_id": CHANNEL, "agent_id": ME}
    )
    assert (row or {}).get("last_read_at") is not None
