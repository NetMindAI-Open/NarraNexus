"""
@file_name: test_patrol_turn.py
@author:
@date: 2026-08-10
@description: The patrol turn itself — when it speaks, and when it stays quiet.

A patrol that narrated every sweep would be a second kind of noise in a room
the product already fought to keep quiet (the folded console, the lingering
activity bubble). So: silence is the normal outcome, and the cursor moves
either way.

Also pinned here: a patrol message is the PLATFORM speaking. It posts under
the room's own marker, not as the lead, which is what keeps it out of the
agent-hop count (owner decision 2026-08-07, option a).
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.team_posting import team_cascade_depth
from narranexus.platform.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.utils.timezone import utc_now


CHANNEL = "ch_room"
TEAM = "t1"


async def _seed_room(db):
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "room", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, name in (("agent_lead", "Ana"), ("agent_worker", "Bruno")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": name,
                                   "created_by": "usr_1"})
    await db.insert("teams", {
        "team_id": TEAM, "owner_user_id": "usr_1", "name": "Desk",
        "lead_agent_id": "agent_lead",
    })


def _trigger(db, reply: str):
    """A trigger whose runtime returns `reply` and records the prompt it got."""
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db._backend))
    seen: dict = {}

    async def _invoke(**kwargs):
        seen.update(kwargs)
        return TurnResult(text=reply, event_id="evt_patrol")

    t._invoke_runtime = _invoke  # type: ignore[method-assign]
    return t, seen


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )


@pytest.mark.asyncio
async def test_a_quiet_patrol_says_nothing_but_moves_the_cursor(db_client):
    """Nothing wrong → nothing posted. The sweep still counts as done.

    A patrol that announced "all good" every ten minutes would be exactly the
    standing noise the room's design keeps removing.
    """
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger, _ = _trigger(db_client, "")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    msgs = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    assert msgs == []
    team = await db_client.get_one("teams", {"team_id": TEAM})
    assert team["last_patrol_at"]


@pytest.mark.asyncio
async def test_a_patrol_with_something_to_say_posts_it(db_client):
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger, _ = _trigger(db_client, "@Bruno 那个 OCR 还在吗?")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    msgs = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    assert len(msgs) == 1
    assert msgs[0]["msg_type"] == PATROL_MSG_TYPE
    # Posted by the ROOM, not by the lead — that is what keeps it out of the
    # agent-hop count.
    assert msgs[0]["from_agent"] == f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}"


@pytest.mark.asyncio
async def test_the_cursor_moves_even_when_the_turn_fails(db_client):
    """A crashed patrol still consumed its slot; re-running it immediately
    would turn one broken team into a hot loop."""
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))

    async def _boom(**kwargs):
        raise RuntimeError("provider exploded")

    trigger._invoke_runtime = _boom  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    team = await db_client.get_one("teams", {"team_id": TEAM})
    assert team["last_patrol_at"]


@pytest.mark.asyncio
async def test_the_speech_cap_silences_a_looping_patrol(db_client):
    """Past the cap the sweep still happens — it just stops posting."""
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    from narranexus.platform.message_bus.patrol import PATROL_SPEECH_MAX

    await db_client.update("teams", {"team_id": TEAM}, {
        "patrol_spoke_at": utc_now(), "patrol_spoke_count": PATROL_SPEECH_MAX,
    })
    trigger, _ = _trigger(db_client, "@Bruno 还在吗?")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    assert await db_client.get("bus_messages", {"channel_id": CHANNEL}) == []
    team = await db_client.get_one("teams", {"team_id": TEAM})
    assert team["last_patrol_at"]


@pytest.mark.asyncio
async def test_the_patrol_prompt_carries_the_board_and_the_stalled_facts(db_client):
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR the scans",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger, seen = _trigger(db_client, "")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    prompt = seen["prompt"]
    assert "OCR the scans" in prompt
    # The stall is stated as a platform fact, and the lead is told what it may
    # do about it — chase, not re-assign (owner decision 2026-08-07).
    assert "stalled" in prompt.lower()
    assert "reassign" not in prompt.lower() or "do not reassign" in prompt.lower()

    # The KEEP side of the delivery-block split: the surface-independent writing
    # rules patrol also needs must STAY on a patrol prompt (the gate only removes
    # the message_team delivery mechanism). Without these, patrol could promise
    # future delivery (the Dunhuang P0) or @mention a non-member.
    assert "Do not promise future delivery" in prompt
    assert "You may ONLY @mention a current channel member" in prompt
    # And those rules must not orphan onto the message history: the split pulled
    # their blank line + header out of the gated block, so the rule bullets keep
    # a paragraph head on a patrol turn (where the gated block is absent).
    plines = prompt.splitlines()
    narrate_idx = next(
        i for i, ln in enumerate(plines) if ln.startswith("- Do NOT narrate")
    )
    assert plines[narrate_idx - 1] == "When you write for this room:"
    assert plines[narrate_idx - 2] == ""


@pytest.mark.asyncio
async def test_the_patrol_prompt_forbids_both_bus_verbs_and_never_orders_message_team(
    db_client,
):
    """A patrol turn has BOTH bus send verbs off the desk (`disallowed_tools`
    returns `[message_agent, message_team]`), so the prompt must (a) forbid both,
    not just `message_team`, and (b) NOT also carry the ordinary-turn instruction
    "Speak in this room by calling message_team(...)". Ordering the very tool it
    just forbade is the self-contradiction that made a patrol turn search-miss
    exactly as the 2026-08-20 incident did — plus the "nothing outside the call
    reaches the room" line is literally false on patrol, where the platform posts
    the plain text as the room.
    """
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR the scans",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger, seen = _trigger(db_client, "")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)
    prompt = seen["prompt"]

    # (a) the forbidding line names BOTH verbs. Extract the line — a whole-prompt
    # `in` would pass on a non-patrol prompt too, where `message_team(` appears
    # in the delivery instruction; here that instruction must be absent (b).
    forbid_line = next(ln for ln in prompt.splitlines() if "do NOT call" in ln)
    assert "message_team" in forbid_line
    assert "message_agent" in forbid_line

    # (b) the ordinary-turn message_team delivery instruction is gated out.
    assert "Speak in this room by calling message_team" not in prompt
    assert "Nothing you write outside that call reaches the room" not in prompt


@pytest.mark.asyncio
async def test_a_patrol_message_does_not_raise_the_cascade_depth(db_client):
    """The exemption that makes patrol work at all.

    A dead flow IS a long unbroken run of agent messages, so depth sits at the
    cap; if patrol's own line counted, its chase @ would be stripped in exactly
    the situation it exists for.
    """
    await _seed_room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    for i in range(3):
        await bus.send_message(from_agent="agent_worker", to_channel=CHANNEL,
                               content=f"hop {i}")
    trigger = MessageBusTrigger(bus=bus)
    before = await team_cascade_depth(db_client, CHANNEL)

    await bus.send_message(
        from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}", to_channel=CHANNEL,
        content="@Bruno still there?", msg_type=PATROL_MSG_TYPE,
    )

    assert await team_cascade_depth(db_client, CHANNEL) == before


@pytest.mark.asyncio
async def test_a_user_message_still_resets_the_depth(db_client):
    """The exemption must not break the thing the counter is for."""
    await _seed_room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    await bus.send_message(from_agent="agent_worker", to_channel=CHANNEL, content="a")
    await bus.send_message(from_agent="usr_1", to_channel=CHANNEL, content="hi")
    trigger = MessageBusTrigger(bus=bus)

    assert await team_cascade_depth(db_client, CHANNEL) == 0


@pytest.mark.asyncio
async def test_patrol_lines_do_not_eat_the_depth_window(db_client):
    """Skipping patrol rows in Python was not enough — they still ate the window.

    Re-pointed 2026-08-18 from `MessageBusTrigger._team_cascade_depth` to
    `team_posting.team_cascade_depth`. The trigger's copy had no production
    caller once posting became a tool call, so this file was asserting the
    invariant against dead code: the SQL exclusion could have been deleted from
    the LIVE query and the suite would have stayed green.

    The depth query is a fixed `LIMIT MAX_TEAM_AGENT_HOPS + 2`. A skipped row
    still consumed one of those slots, so with enough patrol lines interleaved
    the countable hops could never reach the cap, and the runaway-@ protection
    stopped applying — precisely in the rooms patrol frequents, since it only
    speaks where a chain is already looping.
    """
    from narranexus.platform.message_bus.team_posting import MAX_TEAM_AGENT_HOPS

    await _seed_room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    # `MAX_TEAM_AGENT_HOPS` real hops (oldest), then 5 patrol lines (newest).
    # The depth query reads a fixed `LIMIT MAX_TEAM_AGENT_HOPS + 2`, so those 5
    # newest patrol rows are 3 more than the window's slack: if patrol rows were
    # counted, they would push 3 real hops OUT of the window and the count could
    # never reach the cap. With the SQL exclusion, patrol rows never occupy a
    # slot, so all `MAX_TEAM_AGENT_HOPS` real hops are seen.
    for i in range(MAX_TEAM_AGENT_HOPS):
        await bus.send_message(from_agent="agent_worker", to_channel=CHANNEL,
                               content=f"hop{i}")
    for i in range(5):
        await bus.send_message(
            from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}", to_channel=CHANNEL,
            content=f"sweep{i}", msg_type=PATROL_MSG_TYPE,
        )
    trigger = MessageBusTrigger(bus=bus)

    # EXACTLY the cap: counting the patrol rows themselves (SQL exclusion
    # deleted) would return `MAX_TEAM_AGENT_HOPS + 2` here, not the cap, because
    # the window then fills with 5 patrol + (cap - 3) hops. The reading that
    # distinguishes the two is the count, not a threshold.
    assert await team_cascade_depth(db_client, CHANNEL) == MAX_TEAM_AGENT_HOPS


@pytest.mark.asyncio
async def test_a_capped_patrol_does_not_run_the_turn_at_all(db_client):
    """The cap gates the TURN, not just the message.

    Checking only at post time still ran a full LLM turn and threw the output
    away: with a stalled board the pace is 180s against a cap of 6 per 30
    minutes, so roughly four entire runs per window were burned for nothing —
    right next to the "empty board, zero runs" guarantee this feature makes.
    """
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    from narranexus.platform.message_bus.patrol import PATROL_SPEECH_MAX

    await db_client.update("teams", {"team_id": TEAM}, {
        "patrol_spoke_at": utc_now(), "patrol_spoke_count": PATROL_SPEECH_MAX,
    })
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    ran = {"count": 0}

    async def _invoke(**kwargs):
        ran["count"] += 1
        return TurnResult(text="@Bruno 还在吗?", event_id="evt_x")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    assert ran["count"] == 0, "capped patrol must not spend an LLM turn"
    # The cursor still moves, or a capped team becomes a hot candidate.
    assert (await db_client.get_one("teams", {"team_id": TEAM}))["last_patrol_at"]


# ── the turn's identity ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_patrol_turn_carries_its_team_identity(db_client):
    """Without `team_id` the board tools cannot prove which room they are in.

    The patrol prompt tells the lead to close delivered items with
    `team_work_complete`. That tool learns its team from the server-injected
    MCP identity — a model parameter would let any turn claim any team. Drop
    the argument here and the platform is asking for a tool call it has made
    unanswerable.
    """
    await _seed_room(db_client)
    trigger, seen = _trigger(db_client, "")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    assert seen["team_id"] == TEAM


@pytest.mark.asyncio
async def test_the_patrol_turn_mirrors_itself_into_bus_activity(db_client):
    """A patrol is a turn in a team room, so it leaves the same trace as one.

    Two readers depend on this row, and both broke while it was missing: the
    board tools' fallback room resolver (every tool answered "not found") and
    the roster (the lead showed idle for the whole patrol).

    Deliberately NOT a third: `detect_stalled_items` does not consult this row
    for the sweeper — see `test_a_lead_does_not_stall_its_own_work_by_patrolling`.
    Opening the row does not make the sweeper's own activity meaningful; it
    only changes which wrong answer you get.
    """
    await _seed_room(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    during: dict = {}

    async def _invoke(**kwargs):
        row = await db_client.get_one(
            "bus_agent_activity", {"agent_id": "agent_lead"}
        )
        during.update(row or {})
        return TurnResult(text="", event_id="evt_patrol")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    assert during.get("state") == "running"
    assert during.get("channel_id") == CHANNEL
    # And it is handed back on the way out, however the turn ended.
    after = await db_client.get_one("bus_agent_activity", {"agent_id": "agent_lead"})
    assert (after or {}).get("state") == "idle"


@pytest.mark.asyncio
async def test_a_capped_patrol_still_updates_the_board(db_client):
    """The speech cap limits SPEAKING, not seeing.

    When the cap gated detection too, a capped team stopped refreshing its
    board: items that went quiet during the capped window still read
    `in_progress` afterwards, so the user's panel under-reported and the
    adaptive interval stayed slow exactly when things were going wrong.
    """
    from datetime import timedelta

    from narranexus.platform.message_bus.patrol import PATROL_SPEECH_MAX
    from narranexus.platform.schema.team_work_schema import WorkItemStatus

    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    item = await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                                  created_by="agent_lead", assignee_id="agent_worker")
    # The assignee has been silent long enough to count as stalled.
    await db_client.insert("bus_agent_activity", {
        "agent_id": "agent_worker", "channel_id": CHANNEL, "state": "idle",
        "updated_at": utc_now() - timedelta(hours=2),
    })
    # Burn the whole speech budget for the current window. The cap lives on the
    # team row (a counter + a window stamp), not on a message count.
    await db_client.update("teams", {"team_id": TEAM}, {
        "patrol_spoke_at": utc_now(),
        "patrol_spoke_count": PATROL_SPEECH_MAX,
    })

    called = False

    async def _invoke(**kwargs):
        nonlocal called
        called = True
        return TurnResult(text="something", event_id="evt_patrol")

    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    # No LLM turn — that is what the cap is for.
    assert called is False
    # But the board learned the truth anyway.
    assert (await repo.get(item.item_id)).status == WorkItemStatus.STALLED


# ── the sweep is a run like any other ───────────────────────────────────────

@pytest.mark.asyncio
async def test_the_sweep_writes_its_run_id_back_into_the_activity_row(db_client):
    """`start()` blanks `event_id`; something has to fill it back in.

    The roster's idle branch hands the frontend this column as the entry point
    into a run's event log. A sweep that opened the row and never wrote its run
    id left the lead's row with `event_id = NULL` permanently — so opening the
    row for the roster's benefit would have cost the roster its link.
    """
    await _seed_room(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))

    async def _invoke(**kwargs):
        await kwargs["on_event_id"]("evt_sweep")
        return TurnResult(text="", event_id="evt_sweep")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    row = await db_client.get_one("bus_agent_activity", {"agent_id": "agent_lead"})
    assert (row or {}).get("event_id") == "evt_sweep"


@pytest.mark.asyncio
async def test_the_sweep_can_be_stopped(db_client):
    """A patrol burns tokens like any other run, so the owner must be able to
    stop it. The stop arrives through the DB (the click lands in the backend
    process), so the run has to be registered with the watcher — and dropped
    again however the sweep ends, or the poll loop outlives it."""
    from narranexus.platform.agent_runtime.cancel_watcher import get_cancel_watcher

    await _seed_room(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    watcher = get_cancel_watcher(db_client)
    seen: dict = {}

    async def _invoke(**kwargs):
        await kwargs["on_event_id"]("evt_sweep")
        seen["watching"] = "evt_sweep" in watcher._tokens
        seen["token"] = kwargs.get("cancellation")
        return TurnResult(text="", event_id="evt_sweep")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    assert seen["watching"] is True
    assert seen["token"] is not None
    # Unregistered on the way out — a token left behind keeps the poll loop
    # alive for a run that is already gone.
    assert "evt_sweep" not in watcher._tokens


@pytest.mark.asyncio
async def test_a_lead_does_not_stall_its_own_work_by_patrolling(db_client):
    """The sweeper is not evidence about itself.

    `detect_stalled_items` asks "is the assignee live" of `bus_agent_activity`.
    For the agent RUNNING the sweep that row describes the sweep, not the item:
    read before its turn opens it says idle (so the lead stalls its own item
    every single cycle, permanently, and the prompt then tells the lead to
    chase itself), read after it says running (so the lead's items can never
    stall). Neither answer is about the item, so the item is skipped.
    """
    from narranexus.platform.schema.team_work_schema import WorkItemStatus

    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    mine = await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="lead's own",
                                  created_by="agent_lead", assignee_id="agent_lead")
    theirs = await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="worker's",
                                    created_by="agent_lead", assignee_id="agent_worker")
    trigger, _ = _trigger(db_client, "")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    assert (await repo.get(mine.item_id)).status != WorkItemStatus.STALLED
    # The rest of the board is judged normally — the worker never showed up.
    assert (await repo.get(theirs.item_id)).status == WorkItemStatus.STALLED


@pytest.mark.asyncio
async def test_a_capped_sweep_leaves_the_previous_run_link_alone(db_client):
    """A sweep that never runs a turn must not touch the activity row.

    `TurnActivity.start()` writes `event_id: None` and resets `steps` /
    `started_at`. Nothing writes the id back until `on_event_id` fires, which
    only happens if a turn actually runs — so opening the row before the speech
    cap meant a capped sweep silently erased the lead's link to its LAST REAL
    run's event log. That is the same user-visible regression this lane already
    fixed once, arriving through the other door: with a stalled board the pace
    is 180s and the cap is 6 per 30 minutes, so the tail of every window is
    exactly these no-op sweeps.
    """
    from narranexus.platform.message_bus.patrol import PATROL_SPEECH_MAX

    await _seed_room(db_client)
    await db_client.insert("bus_agent_activity", {
        "agent_id": "agent_lead", "channel_id": CHANNEL, "state": "idle",
        "event_id": "evt_last_real_reply",
    })
    await db_client.update("teams", {"team_id": TEAM}, {
        "patrol_spoke_at": utc_now(), "patrol_spoke_count": PATROL_SPEECH_MAX,
    })
    trigger, _ = _trigger(db_client, "should never run")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    row = await db_client.get_one("bus_agent_activity", {"agent_id": "agent_lead"})
    assert row["event_id"] == "evt_last_real_reply"


@pytest.mark.asyncio
async def test_a_stale_stall_on_the_sweepers_own_item_is_cleared(db_client):
    """Skipping the sweeper must mean "pass no verdict", not "touch nothing".

    Reachable without contrivance: an item is assigned to B, B goes dark and an
    earlier sweep marks it `stalled`, then the owner makes B the lead. From
    then on every sweep skips the item, so the stall can never be cleared —
    `ACTIVE` includes `STALLED`, so `has_stalled` pins the team at the 180s
    pace forever, the user's panel shows a permanent `stalled`, and the item is
    excluded from `patrol_stalled` so the lead is never even told to fix it.

    If this row carries no information about the sweeper, then a verdict drawn
    from it earlier has no business outliving that.
    """
    from narranexus.platform.message_bus.patrol import detect_stalled_items
    from narranexus.platform.schema.team_work_schema import WorkItemStatus

    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    item = await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="carried over",
                                  created_by="agent_lead", assignee_id="agent_lead")
    await repo.set_status(item.item_id, WorkItemStatus.STALLED)

    stalled = await detect_stalled_items(
        db_client, TEAM, executor_agent_id="agent_lead"
    )

    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS
    # Cleared, not chased: the sweeper is still not told to nag itself.
    assert [i.item_id for i in stalled] == []


@pytest.mark.asyncio
async def test_patrol_mentions_are_bounded_by_its_speech_cap_not_the_hop_cap(
    db_client,
):
    """Patrol's @mentions skip the hop cap on purpose; something else bounds them.

    The hop cap exists to break agent-to-agent @mention loops. Patrol is the
    opposite case: its job is chasing work that has STALLED, and a stalled chain
    is usually one the cap has already stopped relaying — so applying the cap to
    patrol would mute it exactly when it is needed.

    What makes that safe is the speech cap, and this pins that the safety is real
    rather than assumed: with the speech budget exhausted, patrol posts nothing at
    all, so it cannot re-mention a stalled assignee however deep the room's
    cascade already is. If someone later removes the speech cap, this fails here
    instead of as a room being @-spammed every 180 seconds.
    """
    from narranexus.platform.message_bus.patrol import may_patrol_speak

    await _seed_room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)

    # Drive the room well past the hop cap first: patrol must not be gated on it.
    from narranexus.platform.message_bus.team_posting import (
        MAX_TEAM_AGENT_HOPS,
        team_cascade_depth,
    )

    for i in range(MAX_TEAM_AGENT_HOPS + 2):
        await bus.send_message(
            from_agent="agent_worker", to_channel=CHANNEL, content=f"hop{i}"
        )
    assert await team_cascade_depth(db_client, CHANNEL) >= MAX_TEAM_AGENT_HOPS, (
        "fixture did not reach the cap, so this proves nothing"
    )

    # The speech cap is the live gate, and it still says yes at this point.
    assert await may_patrol_speak(db_client, TEAM) is True, (
        "patrol was silenced by the room's cascade depth — the hop cap is gating "
        "it after all, which mutes stall-chasing exactly when it is needed"
    )
