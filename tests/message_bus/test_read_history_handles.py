"""
@file_name: test_read_history_handles.py
@author:
@date: 2026-08-18
@description: `read_history` takes conversation HANDLES, not channel ids.

The redesign's premise is that an agent's world holds private conversations and
teams — not channels. `read_history(agent_id, channel_id, limit)` was the one
tool that contradicted it, and the contradiction was load-bearing: to call it,
the agent needed an id, so the instruction printed a `### Your Channels` list of
raw `channel_id`s and `channel_type`s into every turn. Removing the list without
changing the tool would have left the tool uncallable; changing the tool is what
let the list go.

The membership assertions are the ones that matter. Both lookups are written so
the AUTHORISATION IS THE QUERY — the DM join requires the caller to be one of
the two members, the team branch requires a `team_members` row — rather than
"find the channel, then check". The second shape is one forgotten branch away
from an agent reading a conversation it is not in, and that branch is exactly
the kind that gets added while fixing something else.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
    _resolve_conversation,
)

ME, PEER, STRANGER = "agent_me", "agent_peer", "agent_stranger"
TEAM, ROOM = "team_1", "ch_team_1"


def _patch_db(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )


async def _dm(db, a, b, channel_id):
    await db.insert(
        "bus_channels",
        {"channel_id": channel_id, "name": f"dm_{a}_{b}",
         "channel_type": "direct", "created_by": a},
    )
    for aid in (a, b):
        await db.insert(
            "bus_channel_members", {"channel_id": channel_id, "agent_id": aid}
        )


@pytest.mark.asyncio
async def test_a_private_conversation_resolves_by_peer(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _dm(db_client, ME, PEER, "ch_dm_1")

    channel_id, err = await _resolve_conversation(ME, with_agent=PEER, team_id="")
    assert err is None
    assert channel_id == "ch_dm_1"


@pytest.mark.asyncio
async def test_a_conversation_i_am_not_in_is_not_found(db_client, monkeypatch):
    """Not "forbidden" — not FOUND. The join is the authorisation.

    A DM between two other agents exists and is perfectly readable by them; the
    query simply cannot return it to a third party, because the caller's own id
    is one of the two things it joins on.
    """
    _patch_db(monkeypatch, db_client)
    await _dm(db_client, PEER, STRANGER, "ch_dm_theirs")

    channel_id, err = await _resolve_conversation(ME, with_agent=STRANGER, team_id="")
    assert channel_id is None
    assert err and STRANGER in err


@pytest.mark.asyncio
async def test_a_team_resolves_to_its_room_for_a_member(db_client, monkeypatch):
    from narranexus.platform.message_bus.team_rooms import team_room_marker

    _patch_db(monkeypatch, db_client)
    await db_client.insert("team_members", {"team_id": TEAM, "agent_id": ME})
    await db_client.insert(
        "bus_channels",
        {
            "channel_id": ROOM,
            "name": "room",
            "channel_type": "group",
            "created_by": team_room_marker(TEAM),
        },
    )

    channel_id, err = await _resolve_conversation(ME, with_agent="", team_id=TEAM)
    assert err is None
    assert channel_id == ROOM


@pytest.mark.asyncio
async def test_a_team_i_am_not_in_is_refused_before_the_room_is_looked_up(
    db_client, monkeypatch
):
    """The room is public-ish (its channel row is findable by team id); the
    membership row is what gates it, and it is checked first."""
    from narranexus.platform.message_bus.team_rooms import team_room_marker

    _patch_db(monkeypatch, db_client)
    await db_client.insert("team_members", {"team_id": TEAM, "agent_id": PEER})
    await db_client.insert(
        "bus_channels",
        {
            "channel_id": ROOM,
            "name": "room",
            "channel_type": "group",
            "created_by": team_room_marker(TEAM),
        },
    )

    channel_id, err = await _resolve_conversation(ME, with_agent="", team_id=TEAM)
    assert channel_id is None
    assert err and "not in team" in err


@pytest.mark.asyncio
async def test_nothing_there_yet_is_an_answer_not_an_exception(db_client, monkeypatch):
    """Both "no such conversation" shapes come back as `(None, message)`.

    The tool turns these into `{"success": false, "error": ...}`; an exception
    here would surface to the agent as a stack-trace string it cannot act on.
    """
    _patch_db(monkeypatch, db_client)

    assert (await _resolve_conversation(ME, with_agent=PEER, team_id=""))[0] is None
    assert (await _resolve_conversation(ME, with_agent="", team_id=TEAM))[0] is None


@pytest.mark.asyncio
async def test_my_own_id_is_refused_rather_than_matching_any_dm(db_client, monkeypatch):
    """`with_agent == self` satisfied BOTH joins.

    The lookup joins the channel against two member rows; passing the caller's own
    id twice matches any direct channel the caller is in, so the tool handed back
    an arbitrary unrelated conversation — silently, and reading as a plausible
    transcript rather than as an error.
    """
    _patch_db(monkeypatch, db_client)
    await _dm(db_client, ME, PEER, "ch_dm_1")

    channel_id, err = await _resolve_conversation(ME, with_agent=ME, team_id="")
    assert channel_id is None
    assert err and "your own id" in err


@pytest.mark.asyncio
async def test_the_dm_lookup_is_ordered_so_two_callers_agree(db_client, monkeypatch):
    """Two `direct` channels for one pair is reachable, so the answer must be stable.

    `send_to_agent` creates a channel when its lookup misses, so two concurrent
    first-sends to the same peer can both miss and both create. With no ORDER BY,
    `rows[0]` is engine-dependent — the sender could write to one channel while
    the history reader reads the other, and the agent gets a plausible but wrong
    transcript instead of an error.
    """
    _patch_db(monkeypatch, db_client)
    await _dm(db_client, ME, PEER, "ch_dm_older")
    await _dm(db_client, ME, PEER, "ch_dm_newer")

    first = await _resolve_conversation(ME, with_agent=PEER, team_id="")
    second = await _resolve_conversation(ME, with_agent=PEER, team_id="")
    assert first == second, "the same question got two different answers"
    assert first[0] == "ch_dm_older", (
        "not the oldest channel — the order is unstable, so `send_to_agent` and "
        "this resolver can disagree about which channel the conversation is"
    )


def test_the_dm_lookup_sql_has_exactly_one_definition():
    """Both callers share the SQL TEXT, each supplying its own placeholder.

    The join lived in two files, differing only in `%s` vs `{ph}` — and "what
    identifies a DM" is exactly the fact that then gets changed in one of them.
    Sharing an `execute()` instead would force one caller onto the wrong
    placeholder, which is how `_room_labels` came to return nothing on SQLite.
    """
    import inspect

    from narranexus.platform.message_bus import local_bus
    from narranexus_plugins.message_bus_module import _message_bus_mcp_tools

    src = inspect.getsource(local_bus) + inspect.getsource(_message_bus_mcp_tools)
    assert src.count("channel_type = 'direct'") == 1, (
        "the DM-channel join has more than one definition again"
    )
    assert "direct_channel_sql" in inspect.getsource(
        _message_bus_mcp_tools._resolve_conversation
    )


@pytest.mark.asyncio
async def test_history_returns_the_recent_page_not_the_rooms_founding_messages(
    db_client, monkeypatch
):
    """The promise is "look further back", and it was answered with the oldest page.

    `get_messages` is `ORDER BY created_at ASC LIMIT n` — the room's OLDEST n. In
    any conversation past `limit` messages the agent asked what happened before
    what it can see and received the founding messages, with an unbounded silent
    hole in between, reading as current context. The primitive that says so is
    `get_recent_messages`, whose own docstring calls `get_messages` "wrong for
    recent scrollback".

    Driven through the registered tool, because the defect was the tool choosing
    the wrong primitive — the primitives themselves were both correct.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus
    from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
        register_message_bus_mcp_tools,
    )

    _patch_db(monkeypatch, db_client)
    await _dm(db_client, ME, PEER, "ch_dm_hist")

    bus = LocalMessageBus(backend=db_client._backend)
    for i in range(12):
        await bus.send_message(from_agent=PEER, to_channel="ch_dm_hist", content=f"m{i}")

    tools = {}

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                tools[fn.__name__] = fn
                return fn
            return _wrap

    async def _bus():
        return bus

    register_message_bus_mcp_tools(_Stub(), _bus)

    result = await tools["read_history"](agent_id=ME, with_agent=PEER, limit=4)
    assert result["success"] is True, result
    got = [m["content"] for m in result["messages"]]
    assert got == ["m8", "m9", "m10", "m11"], (
        f"expected the four most recent, got {got} — the oldest page means a "
        f"silent hole between what the agent sees and what it is handed"
    )


@pytest.mark.asyncio
async def test_the_limit_has_a_ceiling_the_model_does_not_choose(
    db_client, monkeypatch
):
    """`limit` was caller-controlled and unbounded.

    `limit=100000` returned 100k rows into the tool result, blowing the context
    window and killing the turn mid-work. Every other agent-facing read in this
    module is capped; this was the one that left the cap to the model.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus
    from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
        READ_HISTORY_MAX,
        register_message_bus_mcp_tools,
    )

    _patch_db(monkeypatch, db_client)
    await _dm(db_client, ME, PEER, "ch_dm_cap")

    bus = LocalMessageBus(backend=db_client._backend)
    seen = {}

    async def _recent(channel_id, limit=20):
        seen["limit"] = limit
        return []

    bus.get_recent_messages = _recent  # type: ignore[method-assign]

    tools = {}

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                tools[fn.__name__] = fn
                return fn
            return _wrap

    async def _bus():
        return bus

    register_message_bus_mcp_tools(_Stub(), _bus)

    await tools["read_history"](agent_id=ME, with_agent=PEER, limit=100000)
    assert seen["limit"] == READ_HISTORY_MAX, seen

    # And a nonsense value falls back rather than raising at the model.
    for bad in (0, -5, "abc", None):
        seen.clear()
        await tools["read_history"](agent_id=ME, with_agent=PEER, limit=bad)
        assert 1 <= seen["limit"] <= READ_HISTORY_MAX, (bad, seen)


@pytest.mark.asyncio
async def test_sending_to_myself_does_not_land_in_a_peers_channel(db_client):
    """The WRITE path's half of the self-id trap, which the read path already had.

    `direct_channel_sql` joins the channel against two member rows. With the same
    id twice, both joins are satisfied by the SAME row, so any direct channel the
    agent belongs to matches and `rows[0]` is arbitrary. Round 3 proved it:
    `send_to_agent(from="a", to="a")` with a seeded `a↔b` channel landed the row
    in `a↔b`.

    Two consequences, both worse than an error: the note-to-self is delivered to
    peer b, and the wake signal starts a full LLM turn for b with nothing to
    answer. An agent naming itself is an ordinary model error, and `message_agent`
    advertises "the same action whether you are answering someone who just wrote to
    you or starting a conversation of your own", which invites it.

    The read resolver rejected this from the start; the write path shared the SQL
    and not the invariant.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    await _dm(db_client, ME, PEER, "ch_dm_self")
    bus = LocalMessageBus(backend=db_client._backend)

    with pytest.raises(ValueError, match="yourself"):
        await bus.send_to_agent(
            from_agent=ME, to_agent=ME, content="note to self",
        )

    rows = await db_client.get("bus_messages", {"channel_id": "ch_dm_self"})
    assert rows == [], (
        f"a note-to-self landed in the agent's conversation with {PEER}: "
        f"{[r['content'] for r in rows]}"
    )
    # And the peer was not woken for it.
    assert await bus.count_unread(PEER) == 0
