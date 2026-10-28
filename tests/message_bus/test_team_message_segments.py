"""
@file_name: test_team_message_segments.py
@author: NarraNexus
@date: 2026-08-12
@description: Carrying the monologue/reply boundary from the run to the wall.

`run_collector` preserves the boundary; this is the rest of the path — bus
write, storage, read back — so the room can lay deliberation out differently
from an answer.

Two properties are load-bearing and pinned here:

  * `content` does not change. It is what every TEXT consumer reads: the memory
    index, and other agents' scrollback. A rendering feature must not rewrite
    the thing the rest of the system reads.
  * Absent segments are normal, not an error. Every message written before this
    change has none, and per iron rule #2 there is no backfill and no
    compatibility shim — the reader renders those as one block, which is exactly
    today's behaviour.
"""

from __future__ import annotations

import json

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus

CHANNEL = "ch_1"


@pytest.fixture
async def bus(db_client):
    await db_client.insert("bus_channels", {
        "channel_id": CHANNEL, "channel_type": "group",
        "created_by": "team_t1", "name": "T",
    })
    return LocalMessageBus(backend=db_client._backend)


async def _row(db, message_id):
    return await db.get_one("bus_messages", {"message_id": message_id})


@pytest.mark.asyncio
async def test_segments_are_stored_with_the_message(bus, db_client):
    mid = await bus.send_message(
        from_agent="agent_a",
        to_channel=CHANNEL,
        content="thinkinganswering",
        segments=[
            {"kind": "monologue", "text": "thinking"},
            {"kind": "reply", "text": "answering"},
        ],
    )

    row = await _row(db_client, mid)
    assert json.loads(row["segments"]) == [
        {"kind": "monologue", "text": "thinking"},
        {"kind": "reply", "text": "answering"},
    ]


@pytest.mark.asyncio
async def test_content_is_untouched_by_segmenting(bus, db_client):
    """The text every other consumer reads must be byte-identical."""
    mid = await bus.send_message(
        from_agent="agent_a",
        to_channel=CHANNEL,
        content="thinkinganswering",
        segments=[{"kind": "reply", "text": "thinkinganswering"}],
    )

    row = await _row(db_client, mid)
    assert row["content"] == "thinkinganswering"


@pytest.mark.asyncio
async def test_a_message_without_segments_stores_null(bus, db_client):
    """Not an empty list: NULL is "this message predates segments or came from a
    path that has none", and the reader must be able to tell that from "this
    turn produced no segments"."""
    mid = await bus.send_message(
        from_agent="agent_a", to_channel=CHANNEL, content="plain"
    )

    row = await _row(db_client, mid)
    assert row.get("segments") in (None, "")


@pytest.mark.asyncio
async def test_an_empty_segment_list_is_stored_as_null_too(bus, db_client):
    """A silent turn is dropped upstream; if one ever reached here, an empty
    list carries no information a reader could use, so it is not worth a row
    value that looks like data."""
    mid = await bus.send_message(
        from_agent="agent_a", to_channel=CHANNEL, content="plain", segments=[]
    )

    row = await _row(db_client, mid)
    assert row.get("segments") in (None, "")


@pytest.mark.asyncio
async def test_reading_a_message_back_returns_parsed_segments(bus, db_client):
    """Stored as JSON text, handed back as a list — no consumer should have to
    know it was serialised."""
    await bus.send_message(
        from_agent="agent_a",
        to_channel=CHANNEL,
        content="ab",
        segments=[{"kind": "monologue", "text": "a"}, {"kind": "reply", "text": "b"}],
    )

    msgs = await bus.get_messages(CHANNEL, limit=10)
    assert msgs[-1].segments == [
        {"kind": "monologue", "text": "a"},
        {"kind": "reply", "text": "b"},
    ]


@pytest.mark.asyncio
async def test_a_legacy_message_reads_back_as_no_segments(bus, db_client):
    """Every message written before this change. No backfill, no shim: the
    reader gets None and renders one block, which is today's behaviour."""
    await bus.send_message(from_agent="agent_a", to_channel=CHANNEL, content="old")

    msgs = await bus.get_messages(CHANNEL, limit=10)
    assert msgs[-1].segments is None


@pytest.mark.asyncio
async def test_corrupt_segments_do_not_break_the_read(bus, db_client):
    """A hand-edited or half-written row must not take the whole room's
    transcript down — losing the layout of one message is a degradation, losing
    the room is an outage."""
    mid = await bus.send_message(from_agent="agent_a", to_channel=CHANNEL, content="x")
    await db_client.update("bus_messages", {"message_id": mid}, {"segments": "{not json"})

    msgs = await bus.get_messages(CHANNEL, limit=10)
    assert msgs[-1].segments is None
    assert msgs[-1].content == "x"


def test_the_bus_signature_appends_rather_than_inserts():
    """`send_message` has positional callers. A parameter added in the middle
    would silently rebind every one of them — this codebase has already paid
    that exact tax once, on ContextRuntime.
    """
    import inspect

    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    sig = inspect.signature(LocalMessageBus.send_message)
    positional = [
        name for name, p in sig.parameters.items()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    # `segments` closes the positional list; anything after it must be
    # keyword-only (`*`), which by construction cannot rebind a positional
    # caller — the multipart fields (2026-09-09) are the first such.
    assert positional[-1] == "segments"


# ── the wiring, end to end ──────────────────────────────────────────────────
#
# Every test above stubs one side or the other. The seam between them is where
# this feature would silently do nothing: my first attempt read `collection` in
# a scope that had no such name, and `getattr(collection, "segments", None)`
# would have swallowed that into a permanent None — feature dead, no error, no
# log, tests green.


def test_a_team_reply_carries_no_segments_and_that_is_correct():
    """RETIRED-AND-REPLACED 2026-08-17, and something is genuinely lost here.

    These two tests pinned the monologue/reply boundary travelling from the run to
    the wall: `_deliver_reply` passed `joined_segments(live_segments)` so the room
    could lay deliberation out differently from the answer.

    That boundary no longer EXISTS for a team reply. The reply is now the argument
    of a tool call, and the agent's deliberation stays private — so there is no
    "monologue half" of a room message to render. The room showing an agent
    thinking out loud is a feature this change removes, deliberately: it existed
    only because the reply and the thinking were the same text.

    The storage half of this file is untouched and still passes — `segments` is
    still a real column with real semantics (absent = one block), and the bulletin
    and IM paths can still use it. What is pinned here is that the ROOM path
    writes none, so nobody re-adds a half-wired boundary later and wonders why the
    panel shows nothing.
    """
    import inspect

    from narranexus.platform.message_bus import team_posting

    src = inspect.getsource(team_posting.post_team_reply)

    assert "segments" not in src, (
        "post_team_reply grew a segments argument — if the room is meant to "
        "render deliberation again, the boundary has to come from somewhere real, "
        "not from re-splitting the tool argument"
    )


async def test_the_route_passes_segments_to_the_panel(db_client):
    """A column the API does not return is a column the UI cannot render."""
    import inspect

    from narranexus_plugins.teams import routes as mod

    src = inspect.getsource(mod)
    assert '"segments": m.segments' in src


def test_bus_messages_are_never_updated_in_place():
    """The assumption the team room's incremental polling rests on.

    The panel used to refetch all 200 messages every 3 seconds, which was
    idempotent by construction. Sending `since` and merging is only equivalent
    while a message, once written, never changes: an append-only merge cannot
    see an edit, so an edit would silently never reach the screen.

    Asserted against the source rather than by trying to perform an edit,
    because the property is "no such path exists" — a behavioural test can only
    show that the paths I happened to think of do not do it.

    If this ever needs to change, `mergeTeamMessages` is the file that has to
    change with it.
    """
    import pathlib

    from tests._paths import iter_engine_py_files

    root = pathlib.Path(__file__).resolve().parents[2]
    # Batch 6 moved the message-bus and teams packages out of src/ and
    # backend/ into plugins/*/src — scanning only the old two roots would
    # miss an in-place update written inside a plugin package entirely.
    scanned = list(iter_engine_py_files())
    assert len(scanned) >= 500, (
        f"only {len(scanned)} engine files scanned — the engine source roots "
        "shrank and this guard is scanning far less than the codebase"
    )
    offenders = []
    for path in scanned:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if 'update("bus_messages"' in text or "update('bus_messages'" in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == [], (
        f"bus_messages is updated in place by {offenders}; the team room's "
        f"incremental merge assumes append-only and would never show the edit"
    )


# ── the seam, driven for real ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_real_team_turn_stores_a_row_without_segments(db_client, monkeypatch):
    """End-to-end on the new path: the agent calls the tool, the row lands, no
    segments — and `content` is exactly what the agent passed.

    Rewritten from `test_a_real_team_turn_stores_its_segments`, which drove the
    retired deliverer. The property worth keeping end-to-end is not the boundary
    (gone, see above) but that `content` is untouched by the room path, because
    it is what every text consumer reads: the memory index and other agents'
    scrollback.
    """
    from ._team_turn import speak_in_room
    from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX

    await db_client.insert("bus_channels", {
        "channel_id": "ch_seg_room", "name": "R", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}team_seg",
    })
    await db_client.insert(
        "bus_channel_members", {"channel_id": "ch_seg_room", "agent_id": "agent_a"}
    )
    await db_client.insert(
        "agents", {"agent_id": "agent_a", "agent_name": "Ana", "created_by": "u1"}
    )
    bus = LocalMessageBus(backend=db_client._backend)

    await speak_in_room(
        db=db_client, bus=bus, agent_id="agent_a", team_id="team_seg",
        channel_id="ch_seg_room", text="the answer itself", event_id="evt_x",
    )

    rows = await db_client.get("bus_messages", {"channel_id": "ch_seg_room"})
    mine = [r for r in rows if r["from_agent"] == "agent_a"]
    assert len(mine) == 1
    assert mine[0]["content"] == "the answer itself"
    assert mine[0].get("segments") in (None, "")
    assert mine[0]["event_id"] == "evt_x"


def test_the_room_funnel_carries_everything_a_room_caller_can_send():
    """`_post_to_room` must not fall behind `send_message`.

    The funnel lists its parameters explicitly, on purpose: `**kwargs` hides a
    misspelled keyword from the type checker and only surfaces it as a runtime
    TypeError. The cost of that choice is that the list can fall behind — and it
    did, for one merge, in the worst possible way: `segments` was added to the
    reply on one branch while the funnel was introduced on another, git merged
    both cleanly, and every team turn then raised TypeError inside the poster,
    was caught by the caller's "the room will never show this" handler, and
    posted a delivery-failure notice instead of the agent's words.

    So the drift is asserted rather than trusted to a comment. The exemptions
    are the parameters a ROOM post has no use for, each for its own reason —
    listing them here is what makes adding a genuinely room-relevant parameter
    fail this test instead of failing in production.
    """
    import inspect

    from narranexus.platform.message_bus.cloud_bus import CloudMessageBus
    from narranexus.platform.message_bus.local_bus import LocalMessageBus
    from narranexus.platform.message_bus.message_bus_service import MessageBusService
    from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger

    # Not routed through the room funnel, and why:
    #   attachments        — the trigger never posts files; the user's uploads
    #                        come in through the REST route, not from a turn.
    #   sender_turn_source — describes the SENDER's errand, which a room post
    #                        does not have: the room is the surface, not a peer.
    #   root_run_id        — cascade lineage, stamped by the agent's own bus
    #                        sends; a room reply's lineage rides the trigger.
    #   routed_by          — records why `mentions` holds what it does, and the
    #                        funnel's callers compute mentions themselves.
    #   part_index /       — a long message sent in parts is a PEER send made
    #   part_count           by a model that hit its output budget; a room post
    #                        is the platform's own line and never a fragment.
    exempt = {
        "attachments", "sender_turn_source", "root_run_id", "routed_by",
        "part_index", "part_count",
    }

    # Anchored on the PROTOCOL, not on one implementation: `_post_to_room` calls
    # `self._bus.send_message`, and the bus in production is whichever one the
    # deployment wired up. An implementation that gained a parameter the protocol
    # did not would be invisible to a test that only reads the local one.
    def _params(fn) -> set:
        return set(inspect.signature(fn).parameters) - {"self"}

    sends = _params(MessageBusService.send_message)
    funnel = _params(MessageBusTrigger._post_to_room)

    # And the implementations must not drift from the protocol either — the two
    # `# Pinned by test_team_message_segments` comments in local_bus and
    # cloud_bus were, until this line, promises nothing checked.
    for impl in (LocalMessageBus, CloudMessageBus):
        assert _params(impl.send_message) == sends, (
            f"{impl.__name__}.send_message has drifted from MessageBusService"
        )

    missing = sends - funnel - exempt
    assert not missing, (
        f"{sorted(missing)} can be sent to a channel but cannot be sent to a ROOM. "
        f"Either add them to `_post_to_room` or name them in this test's exemption "
        f"list with the reason — the one time this drifted, every team reply became "
        f"a delivery-failure notice."
    )
