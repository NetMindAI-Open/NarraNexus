"""
@file_name: test_errand_auto_board.py
@author:
@date: 2026-08-14
@description: The errand layer — a hand-off gets onto the board without anyone
              remembering to put it there.

The work board (#259) can already notice a stalled task, tighten its cadence
and chase the assignee. Its ENTRANCE, though, was `work_add_item`: a tool the
Leader has to remember to call. So the whole guard rested on model obedience —
the dependency iron rule #15 exists to keep off correctness-critical paths —
and the founding case walks straight through it: nobody called the tool during
the Dunhuang chain, so the board was empty and patrol had nothing to sweep.

This is the message-level half the owner specified on 2026-08-07: "an errand is
a MESSAGE-level fact recorded automatically (A @ B opens it, B's reply closes
it)". Opening without closing would be strictly worse than nothing — every
casual "@Bruno 你怎么看" would pile onto the board and patrol would chase it
forever — so the two halves ship together and are tested together.

The one rule that carries the incident: **a promise does not close an errand.**
"收到，开始处理……完成后交付 @A4" is the exact text that ended the Dunhuang run,
and treating it as delivery would hand the guard back to the failure it exists
to catch.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from narranexus.platform.message_bus.errand import (
    close_delivered_errands,
    is_promise_only,
    record_handoffs,
)
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemOrigin, WorkItemStatus
from narranexus.platform.utils.timezone import utc_now


TEAM = "team_dunhuang"
CHANNEL = "ch_dunhuang"
LEAD = "agent_leader"
A3 = "agent_a3"
A4 = "agent_a4"

# The message that ended the real run (evt_41c581a8fe8a4bac), shortened.
PROMISE = "收到，开始处理。……完成后产出 03_normalised.json 并交付 @A4"


@pytest.fixture
def repo(db_client):
    return TeamWorkItemRepository(db_client)


async def _open_errand(db, *, from_agent, to_agent, text="handle the OCR output",
                       message_id="msg_1", lead_agent_id=LEAD):  # noqa: D103
    return await record_handoffs(
        db,
        team_id=TEAM,
        channel_id=CHANNEL,
        from_agent=from_agent,
        mentions=[to_agent],
        text=text,
        message_id=message_id,
        root_run_id="evt_root",
        lead_agent_id=lead_agent_id,
    )


# ===========================================================================
# Opening
# ===========================================================================

@pytest.mark.asyncio
async def test_an_at_mention_opens_an_errand_nobody_had_to_declare(db_client, repo):
    """The entrance that does not depend on the Leader remembering a tool."""
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    assert len(opened) == 1
    item = await repo.get(opened[0])
    assert item is not None
    assert item.assignee_id == A3
    assert item.created_by == LEAD
    assert item.status == WorkItemStatus.IN_PROGRESS
    assert item.origin == WorkItemOrigin.AUTO
    assert item.source_message_id == "msg_1"
    assert item.root_run_id == "evt_root"


@pytest.mark.asyncio
async def test_the_title_is_the_ask_without_the_at_noise(db_client, repo):
    """The board is read by a model every turn; `@A4 @A5 do the thing` as a
    title spends its budget on markup."""
    opened = await _open_errand(
        db_client, from_agent=LEAD, to_agent=A3,
        text="@A3 normalise the OCR output into 03_normalised.json\nthen tell me",
    )

    item = await repo.get(opened[0])
    assert item.title.startswith("normalise the OCR output")
    assert "@A3" not in item.title
    assert "\n" not in item.title


@pytest.mark.asyncio
async def test_the_same_message_never_opens_twice(db_client, repo):
    """The poll loop can re-deliver, and a retried post keeps its message id.

    Without this the board grows a duplicate on every retry and patrol chases
    the same hand-off several times over.
    """
    first = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)
    again = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    assert len(first) == 1
    assert again == []
    assert len(await repo.list_active(TEAM)) == 1


@pytest.mark.asyncio
async def test_broadcast_and_self_mention_are_not_hand_offs(db_client, repo):
    """`@everyone` addresses a room, not a person — nobody is late on it.

    A self-mention is not an assignment either, and would make an agent chase
    itself on the next patrol.
    """
    assert await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=LEAD, lead_agent_id=LEAD,
        mentions=["@everyone"], text="standup in 5", message_id="m_b",
        root_run_id="",
    ) == []
    assert await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=LEAD, lead_agent_id=LEAD,
        mentions=[LEAD], text="note to self", message_id="m_s", root_run_id="",
    ) == []
    assert await repo.list_active(TEAM) == []


@pytest.mark.asyncio
async def test_one_post_can_hand_off_to_several_people(db_client, repo):
    """Each assignee is late on their own, so each gets an item."""
    opened = await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=LEAD, lead_agent_id=LEAD,
        mentions=[A3, A4], text="split the batch between you",
        message_id="m_multi", root_run_id="",
    )

    assert len(opened) == 2
    assert {i.assignee_id for i in await repo.list_active(TEAM)} == {A3, A4}


# ===========================================================================
# Closing — and the one case that must NOT close
# ===========================================================================

@pytest.mark.asyncio
async def test_delivering_closes_the_errand(db_client, repo):
    """The other half. Without it the board only ever grows."""
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3,
        text="03_normalised.json is in the shared folder — 1,204 pages.",
    )

    assert closed == opened
    assert (await repo.get(opened[0])).status == WorkItemStatus.DONE


@pytest.mark.asyncio
async def test_one_delivery_settles_one_errand(db_client, repo):
    """Owing several things at once is normal in a six-stage pipeline.

    Closing them all on the first delivery is "mistaken for a delivery" in its
    other form — the same failure `is_promise_only` is biased against, so the
    same argument decides it. It also inflated the closure rate (one post,
    three `close` lines), and that rate is the evidence PR #230 wants before
    anyone concludes a stronger fallback is unnecessary.
    """
    first = (await _open_errand(
        db_client, from_agent=LEAD, to_agent=A3, text="page 1-400",
        message_id="msg_1"))[0]
    second = (await _open_errand(
        db_client, from_agent=LEAD, to_agent=A3, text="page 401-800",
        message_id="msg_2"))[0]

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3,
        text="first batch is in the shared folder — 400 pages",
    )

    # The OLDEST: it is both the likeliest referent and the one closest to
    # being chased.
    assert closed == [first]
    assert (await repo.get(first)).status == WorkItemStatus.DONE
    assert (await repo.get(second)).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_the_dunhuang_promise_does_not_close_anything(db_client, repo):
    """The founding case, as one assertion.

    A3 answered and the run ended `completed`. If that text closed the errand,
    the board would agree with the runtime that the work was done — and the
    platform would once again have no idea the pipeline had died.
    """
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3, text=PROMISE,
    )

    assert closed == []
    assert (await repo.get(opened[0])).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_members_promise_that_hands_on_leaves_only_the_leads_link_watched(db_client, repo):
    """A3's own errand stays open; A4's does NOT open (A3 is not the lead).

    This is the composed behaviour of the two halves on the real message, and
    the reason they cannot be reasoned about separately.
    """
    a3_item = (await _open_errand(db_client, from_agent=LEAD, to_agent=A3))[0]

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3, text=PROMISE,
    )
    handed_on = await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=A3,
        mentions=[A4], text=PROMISE, message_id="msg_2", root_run_id="evt_root",
        lead_agent_id=LEAD,
    )

    # The lead's errand for A3 survives A3's promise — that is the link the
    # incident needed watched. A3 → A4 is a member handing on, which since
    # 2026-09-03 wakes A4 but books nothing (see `opens_handoffs`).
    assert closed == []
    assert handed_on == []
    active = {i.assignee_id: i for i in await repo.list_active(TEAM)}
    assert set(active) == {A3}
    assert active[A3].item_id == a3_item


# ===========================================================================
# Who may open one (2026-09-03)
# ===========================================================================

@pytest.mark.asyncio
async def test_a_member_who_is_not_the_lead_opens_nothing(db_client, repo):
    """Discussion between members is not assignment.

    Delete the `opens_handoffs` gate in `record_handoffs` and this goes red:
    A3's "@A4 你怎么看" would book A4 a task every member then reads each
    turn and patrol chases.
    """
    opened = await _open_errand(
        db_client, from_agent=A3, to_agent=A4, text="@A4 你怎么看", lead_agent_id=LEAD,
    )
    assert opened == []
    assert await repo.list_active(TEAM) == []


@pytest.mark.asyncio
async def test_the_lead_opens_one(db_client, repo):
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3, lead_agent_id=LEAD)
    assert len(opened) == 1
    assert [i.assignee_id for i in await repo.list_active(TEAM)] == [A3]


@pytest.mark.asyncio
async def test_a_team_without_a_lead_books_nothing_from_agents(db_client, repo):
    """No lead → patrol is off (`patrol_is_on`), so nobody would sweep it."""
    opened = await _open_errand(db_client, from_agent=A3, to_agent=A4, lead_agent_id=None)
    assert opened == []
    assert await repo.list_active(TEAM) == []


@pytest.mark.asyncio
async def test_the_user_opens_one_whoever_the_lead_is(db_client, repo):
    for lead in (LEAD, None):
        opened = await _open_errand(
            db_client, from_agent="usr_owner", to_agent=A4,
            message_id=f"msg_user_{lead}", lead_agent_id=lead,
        )
        assert len(opened) == 1, f"lead={lead!r}"


def test_opens_handoffs_is_user_or_lead_only():
    from narranexus.platform.message_bus.errand import opens_handoffs

    assert opens_handoffs("usr_x", None) is True
    assert opens_handoffs("usr_x", LEAD) is True
    assert opens_handoffs(LEAD, LEAD) is True
    assert opens_handoffs(A3, LEAD) is False
    assert opens_handoffs(A3, None) is False
    assert opens_handoffs("", LEAD) is False


@pytest.mark.asyncio
async def test_the_post_path_reads_the_lead_from_the_team_row(db_client, repo, monkeypatch):
    """`post_team_reply` resolves the lead itself — the tool passes none.

    Two posts through the real seam, same text, different senders: the lead's
    books A3, the member's books nothing. Both @mentions are DELIVERED (the
    bus row carries them) — activation is untouched by the gate.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus
    from narranexus.platform.message_bus.team_posting import post_team_reply

    await db_client.insert("teams", {
        "team_id": TEAM, "name": "T", "owner_user_id": "usr_1",
        "lead_agent_id": LEAD,
    })
    await db_client.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "room", "channel_type": "group",
        "created_by": f"team_{TEAM}",
    })
    for aid in (LEAD, A3, A4):
        await db_client.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
    roster = [{"agent_id": LEAD, "name": "Leader"}, {"agent_id": A3, "name": "A3"},
              {"agent_id": A4, "name": "A4"}]
    bus = LocalMessageBus(backend=db_client._backend)

    r_lead = await post_team_reply(
        db=db_client, bus=bus, agent_id=LEAD, team_id=TEAM, channel_id=CHANNEL,
        text="@A3 please pull the numbers", roster=roster,
    )
    assert r_lead["mentioned"] == [A3]
    assert [i.assignee_id for i in await repo.list_active(TEAM)] == [A3]

    # A3 answers with a promise (so its own errand stays open — the existing
    # rule) and hands on to A4: delivered to A4, booked for nobody.
    r_member = await post_team_reply(
        db=db_client, bus=bus, agent_id=A3, team_id=TEAM, channel_id=CHANNEL,
        text=PROMISE, roster=roster,
    )
    assert r_member["mentioned"] == [A4]
    assert [i.assignee_id for i in await repo.list_active(TEAM)] == [A3]


@pytest.mark.asyncio
async def test_a_bare_acknowledgment_does_not_close_either(db_client, repo):
    """"收到" alone is the shortest form of the same lie."""
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    for ack in ("收到", "好的，我来处理", "got it", "OK, on it"):
        assert await close_delivered_errands(
            db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3, text=ack,
        ) == [], f"{ack!r} closed the errand"

    assert (await repo.get(opened[0])).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_tool_made_item_is_never_auto_closed(db_client, repo):
    """The Leader's own board entry is a TASK, and one task spans several
    errands (owner decision 2026-08-07). Closing it on the first message from
    the assignee would delete the layer that decision created.
    """
    item = await repo.create_item(
        team_id=TEAM, channel_id=CHANNEL, title="the whole OCR pipeline",
        created_by=LEAD, assignee_id=A3,
    )

    closed = await close_delivered_errands(
        db_client, team_id=TEAM, channel_id=CHANNEL, agent_id=A3,
        text="first batch done, 400 pages",
    )

    assert closed == []
    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS
    assert item.origin == WorkItemOrigin.TOOL


@pytest.mark.asyncio
async def test_closing_is_scoped_to_this_room(db_client, repo):
    """An agent in two teams must not close one room's errand by speaking in
    the other."""
    opened = await _open_errand(db_client, from_agent=LEAD, to_agent=A3)

    closed = await close_delivered_errands(
        db_client, team_id="team_other", channel_id="ch_other", agent_id=A3,
        text="done over here",
    )

    assert closed == []
    assert (await repo.get(opened[0])).status == WorkItemStatus.IN_PROGRESS


# ===========================================================================
# Bounds — what makes automatic opening survivable
#
# Before this layer, every board row cost a Leader a deliberate tool call, so
# the board was short by construction. Now the row count follows room traffic,
# and every row is rendered into every member's prompt on every turn. Worse,
# `stalled` counts as ACTIVE: one row nobody will ever deliver keeps the team
# on patrol's 180s cadence and spending its speech budget indefinitely, which
# retires the "empty board = zero runs" cost guarantee patrol documents.
# ===========================================================================

@pytest.mark.asyncio
async def test_one_message_cannot_open_unbounded_hand_offs(db_client, repo):
    """Caller-controlled input, previously unbounded.

    Also a latency bound: since the reply moved inside the turn, this
    book-keeping runs while the runtime waits on the delivery callback.
    """
    from narranexus.platform.message_bus.errand import MAX_HANDOFFS_PER_MESSAGE

    many = [f"agent_{i}" for i in range(MAX_HANDOFFS_PER_MESSAGE + 4)]

    opened = await record_handoffs(
        db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=LEAD, lead_agent_id=LEAD,
        mentions=many, text="everyone take a slice", message_id="m_many",
        root_run_id="",
    )

    assert len(opened) == MAX_HANDOFFS_PER_MESSAGE
    assert len(await repo.list_active(TEAM)) == MAX_HANDOFFS_PER_MESSAGE


@pytest.mark.asyncio
async def test_an_undeliverable_errand_does_not_live_forever(db_client, repo):
    """The recycler. Without it a rhetorical @ costs the team its patrol
    cadence permanently — `stalled` is ACTIVE, so the team never goes quiet."""
    from datetime import timedelta

    from narranexus.platform.message_bus.errand import (
        ERRAND_TTL_HOURS,
        expire_stale_errands,
    )
    from narranexus.platform.utils.timezone import utc_now

    opened = (await _open_errand(db_client, from_agent=LEAD, to_agent=A3))[0]
    fresh = (await _open_errand(
        db_client, from_agent=LEAD, to_agent=A4, message_id="msg_fresh"))[0]
    await db_client.update(
        "team_work_items", {"item_id": opened},
        {"created_at": utc_now() - timedelta(hours=ERRAND_TTL_HOURS + 1)},
    )

    expired = await expire_stale_errands(db_client, TEAM)

    assert expired == [opened]
    # Retired as CANCELLED, never DONE: `done` is what the closure-rate report
    # counts as a delivery, so expiring into it would restate "nobody got to
    # this" as "it was delivered" and corrupt the one metric this work adds.
    assert (await repo.get(opened)).status == WorkItemStatus.CANCELLED
    assert (await repo.get(fresh)).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_expiry_survives_both_timestamp_shapes_in_the_column(db_client, repo):
    """`created_at` holds two textual shapes, and a string compare gets it wrong.

    Rows written by the schema default land as `2026-08-17 02:52:40`; anything
    written from Python lands as `2026-08-17T02:52:40+00:00`. Comparing them as
    strings breaks at the separator ('T' > ' '), so the first version of this
    sweep — which put `created_at < %s` in SQL — matched NOTHING on SQLite, the
    desktop build's production backend, and did so silently.

    Both shapes are seeded here explicitly so the Python-side ageing cannot
    regress back into a dialect-dependent predicate.
    """
    from narranexus.platform.message_bus.errand import (
        ERRAND_TTL_HOURS,
        expire_stale_errands,
    )

    sql_shaped = (await _open_errand(
        db_client, from_agent=LEAD, to_agent=A3, message_id="msg_sql"))[0]
    iso_shaped = (await _open_errand(
        db_client, from_agent=LEAD, to_agent=A4, message_id="msg_iso"))[0]

    old = utc_now() - timedelta(hours=ERRAND_TTL_HOURS + 2)
    await db_client.update(
        "team_work_items", {"item_id": sql_shaped},
        # What `(datetime('now'))` writes: space separator, no offset.
        {"created_at": old.strftime("%Y-%m-%d %H:%M:%S")},
    )
    await db_client.update(
        "team_work_items", {"item_id": iso_shaped},
        {"created_at": old},  # serialised by the backend as ISO 8601
    )

    expired = await expire_stale_errands(db_client, TEAM)

    assert set(expired) == {sql_shaped, iso_shaped}


@pytest.mark.asyncio
async def test_a_leaders_own_task_is_never_expired(db_client, repo):
    """A `tool` item is explicit. Making one vanish on a timer is a different
    class of accident from letting an inferred errand lapse."""
    from datetime import timedelta

    from narranexus.platform.message_bus.errand import (
        ERRAND_TTL_HOURS,
        expire_stale_errands,
    )
    from narranexus.platform.utils.timezone import utc_now

    item = await repo.create_item(
        team_id=TEAM, channel_id=CHANNEL, title="the whole pipeline",
        created_by=LEAD, assignee_id=A3,
    )
    await db_client.update(
        "team_work_items", {"item_id": item.item_id},
        {"created_at": utc_now() - timedelta(hours=ERRAND_TTL_HOURS * 10)},
    )

    assert await expire_stale_errands(db_client, TEAM) == []
    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_team_with_no_lead_still_gets_its_errands_recycled(db_client, repo):
    """The asymmetry that made the recycler miss the teams that need it most.

    Opening has no gate; recycling used to sit inside `detect_stalled_items`,
    downstream of `patrol_is_on` — which requires a lead, and `lead_agent_id`
    defaults to None. So a team nobody has put in charge (the state every team
    starts in, and the one least likely to have anyone tidying the board by
    hand) opened errands from every @mention and recycled none of them.

    `teams_with_active_work()` is the right scope precisely because it looks at
    neither the lead nor `patrol_enabled`.
    """
    from narranexus.platform.message_bus.errand import ERRAND_TTL_HOURS
    from narranexus.platform.message_bus.patrol import teams_due_for_patrol

    await db_client.insert("teams", {
        "team_id": TEAM, "owner_user_id": "usr_1", "name": "Leaderless",
        # No lead — the default, not a contrived state.
    })
    opened = (await _open_errand(db_client, from_agent=LEAD, to_agent=A3))[0]
    await db_client.update(
        "team_work_items", {"item_id": opened},
        {"created_at": utc_now() - timedelta(hours=ERRAND_TTL_HOURS + 1)},
    )

    due = await teams_due_for_patrol(db_client)

    # Still not patrolled — the platform does not appoint a lead…
    assert [t for t in due if t[0] == TEAM] == []
    # …but the board was cleaned up anyway.
    assert (await repo.get(opened)).status == WorkItemStatus.CANCELLED


@pytest.mark.asyncio
async def test_the_recycle_happens_before_the_cadence_is_judged(db_client, repo):
    """Ordering: an expired row must not still be counted as stalled.

    `has_stalled` decides between the 600s and 180s cadence and `items[0]`
    decides which room a patrol is aimed at, so reading the board before the
    recycle would let a row that no longer exists drive both.
    """
    from narranexus.platform.message_bus.errand import ERRAND_TTL_HOURS
    from narranexus.platform.message_bus.patrol import teams_due_for_patrol

    await db_client.insert("teams", {
        "team_id": TEAM, "owner_user_id": "usr_1", "name": "Desk",
        "lead_agent_id": LEAD,
    })
    stale_item = (await _open_errand(db_client, from_agent=LEAD, to_agent=A3))[0]
    await db_client.update(
        "team_work_items", {"item_id": stale_item},
        {"created_at": utc_now() - timedelta(hours=ERRAND_TTL_HOURS + 1),
         "status": WorkItemStatus.STALLED},
    )

    due = await teams_due_for_patrol(db_client)

    assert (await repo.get(stale_item)).status == WorkItemStatus.CANCELLED
    # The team's only row is gone, so there is nothing left to patrol for.
    assert [t for t in due if t[0] == TEAM] == []


@pytest.mark.asyncio
async def test_one_sweep_retires_a_bounded_number(db_client, repo):
    """Expiry arrives in bursts, and this sweep now runs inside the poll cycle.

    A room busy yesterday can have hundreds of rows come due in the same
    minute, at two queries each, in front of every agent's message dispatch.
    The remainder is retired on the next cycle — deferred, never dropped, which
    the second call asserts.
    """
    from narranexus.platform.message_bus.errand import (
        ERRAND_TTL_HOURS,
        MAX_EXPIRIES_PER_SWEEP,
        expire_stale_errands,
    )

    old = utc_now() - timedelta(hours=ERRAND_TTL_HOURS + 1)
    total = MAX_EXPIRIES_PER_SWEEP + 3
    for i in range(total):
        opened = (await record_handoffs(
            db_client, team_id=TEAM, channel_id=CHANNEL, from_agent=LEAD, lead_agent_id=LEAD,
            mentions=[f"agent_{i}"], text=f"task {i}", message_id=f"m_{i}",
        ))[0]
        await db_client.update(
            "team_work_items", {"item_id": opened}, {"created_at": old}
        )

    first = await expire_stale_errands(db_client, TEAM)
    second = await expire_stale_errands(db_client, TEAM)

    assert len(first) == MAX_EXPIRIES_PER_SWEEP
    assert len(second) == 3
    assert await repo.list_active(TEAM) == []


@pytest.mark.asyncio
async def test_liveness_in_one_room_does_not_reprieve_another(db_client, repo):
    """`bus_agent_activity` is keyed by (agent, channel), so the cache must be.

    Unreachable while a team has exactly one room, and nothing enforces that —
    `create_item` takes any channel_id. The unsafe direction is the one under
    test: a verdict of "idle over there" cancelling a row here would retire an
    errand that should have stayed, and the report would read it as expired
    rather than delivered.
    """
    from narranexus.platform.message_bus.errand import (
        ERRAND_TTL_HOURS,
        expire_stale_errands,
    )

    other_room = "ch_second_room"
    here = (await _open_errand(
        db_client, from_agent=LEAD, to_agent=A3, message_id="m_here"))[0]
    there = (await record_handoffs(
        db_client, team_id=TEAM, channel_id=other_room, from_agent=LEAD, lead_agent_id=LEAD,
        mentions=[A3], text="the other room", message_id="m_there",
    ))[0]
    old = utc_now() - timedelta(hours=ERRAND_TTL_HOURS + 1)
    for item_id in (here, there):
        await db_client.update(
            "team_work_items", {"item_id": item_id}, {"created_at": old}
        )
    # A3 is running HERE and has no activity row for the other room.
    await db_client.insert("bus_agent_activity", {
        "agent_id": A3, "channel_id": CHANNEL, "state": "running",
        "started_at": old, "updated_at": utc_now(),
    })

    expired = await expire_stale_errands(db_client, TEAM)

    assert expired == [there]
    assert (await repo.get(here)).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_hand_off_still_being_worked_on_is_not_expired(db_client, repo):
    """Age is the trigger; "nobody is on it" is the question.

    Iron rule #14 makes a hand-off that runs for tens of hours legitimate. If
    the row were retired at hour 24, the delivery at hour 30 would close
    nothing and the closure report would book a real hand-off as "expired".
    `detect_stalled_items` reaches for the same evidence for the same reason.
    """
    from narranexus.platform.message_bus.errand import (
        ERRAND_TTL_HOURS,
        expire_stale_errands,
    )

    working = (await _open_errand(
        db_client, from_agent=LEAD, to_agent=A3, message_id="msg_working"))[0]
    gone = (await _open_errand(
        db_client, from_agent=LEAD, to_agent=A4, message_id="msg_gone"))[0]
    old = utc_now() - timedelta(hours=ERRAND_TTL_HOURS + 1)
    for item_id in (working, gone):
        await db_client.update(
            "team_work_items", {"item_id": item_id}, {"created_at": old}
        )
    # A3 has been running for 30 hours and is STILL beating.
    await db_client.insert("bus_agent_activity", {
        "agent_id": A3, "channel_id": CHANNEL, "state": "running",
        "started_at": old, "updated_at": utc_now(),
    })

    expired = await expire_stale_errands(db_client, TEAM)

    assert expired == [gone]
    assert (await repo.get(working)).status == WorkItemStatus.IN_PROGRESS


def test_the_board_section_declares_what_it_hides():
    """Truncation that reads as completeness would have the lead conclude the
    rest was already closed."""
    from narranexus.platform.message_bus.message_bus_trigger import (
        TEAM_BOARD_MAX_ITEMS,
        MessageBusTrigger,
    )
    from narranexus.platform.message_bus.schemas import BusMessage

    board = [
        {"status": "open", "title": f"task {i}", "assignee_id": A3,
         "item_id": f"wi_{i}"}
        for i in range(TEAM_BOARD_MAX_ITEMS + 7)
    ]
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    msg = BusMessage(
        message_id="m1", channel_id=CHANNEL, from_agent="usr_u", content="?"
    )
    text = trigger._build_team_prompt(
        LEAD, [msg],
        [{"agent_id": LEAD, "name": "Ana"}, {"agent_id": A3, "name": "A3"}],
        owner_user_id="usr_u", team_id=TEAM, trigger_messages=[msg],
        lead_agent_id=LEAD, work_items=board, bulletin=None,
    )

    assert "task 0" in text
    assert f"task {TEAM_BOARD_MAX_ITEMS + 6}" not in text
    assert "+7 more not shown" in text


# ===========================================================================
# Wiring — the layer is useless if the delivery path never calls it
# ===========================================================================

@pytest.mark.asyncio
async def test_a_real_team_reply_records_its_errand(db_client, monkeypatch, repo):
    """Through the production posting path, not through the helpers directly.

    Every assertion above would still pass with the module unreferenced. This
    is the one that fails if the delivery path forgets to call it.

    That path moved on 2026-08-17. A team reply used to be the agent's PLAIN
    TEXT, posted by the trigger, so this test drove `_handle_channel_batch`
    with a stub that invoked the `on_plain_text_delivery` callback. The room
    takes a tool call now: `message_team` -> `team_posting.post_team_reply`,
    which is where the hop cap, @mention resolution and this book-keeping all
    live. Driving the trigger would exercise a path production no longer
    takes — the exact failure this test was written to catch, one contract
    change later.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    from ._team_turn import speak_in_room

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )
    for aid, name in ((LEAD, "Leader"), (A4, "A4")):
        await db_client.insert(
            "agents", {"agent_id": aid, "agent_name": name, "created_by": "usr_1"}
        )
        await db_client.insert(
            "bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid}
        )
    # The posting path reads who the lead is from the team row (2026-09-03).
    await db_client.insert("teams", {
        "team_id": TEAM, "name": "T", "owner_user_id": "usr_1", "lead_agent_id": LEAD,
    })

    bus = LocalMessageBus(backend=db_client._backend)
    await speak_in_room(
        db=db_client, bus=bus, agent_id=LEAD, team_id=TEAM, channel_id=CHANNEL,
        text="OCR is normalised — @A4 take it from here", event_id="evt_1",
    )

    items = await repo.list_active(TEAM)
    assert [i.assignee_id for i in items] == [A4]
    assert items[0].origin == WorkItemOrigin.AUTO
    # Keyed to the message the room actually shows, so the row is traceable.
    assert items[0].source_message_id


@pytest.mark.asyncio
async def test_bookkeeping_never_breaks_a_delivered_reply(db_client, monkeypatch):
    """The reply is already in the room by the time this layer runs.

    Letting a board write fail the hop would trade a working delivery for
    bookkeeping — the opposite of the trade this whole feature is making.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    from ._team_turn import speak_in_room

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )
    await db_client.insert(
        "agents", {"agent_id": LEAD, "agent_name": "L", "created_by": "usr_1"}
    )
    await db_client.insert(
        "bus_channel_members", {"channel_id": CHANNEL, "agent_id": LEAD}
    )

    async def _boom(*_a, **_k):
        raise RuntimeError("board is on fire")

    monkeypatch.setattr(
        "narranexus.platform.message_bus.errand.record_handoffs", _boom
    )

    bus = LocalMessageBus(backend=db_client._backend)
    # Must REPORT success too: a book-keeping failure that made the tool return
    # an error would file a landed reply as lost — the confusion this exists to
    # prevent — and the agent would say it again.
    result = await speak_in_room(
        db=db_client, bus=bus, agent_id=LEAD, team_id=TEAM, channel_id=CHANNEL,
        text="here is the answer", event_id="evt_1",
    )
    assert result.get("message_id")

    rows = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    assert any(r["content"] == "here is the answer" for r in rows), (
        "a board failure swallowed the reply"
    )


@pytest.mark.asyncio
async def test_the_hook_sits_outside_the_post(db_client, monkeypatch):
    """Pins the CALL SITE, which the test above cannot.

    That one patches `errand.record_handoffs`, and `_record_errands` swallows
    everything internally — so it stays green even if the call is moved to the
    near side of `bus.send_message`. Two safeties are in play (position AND the
    internal swallow) and each needs its own test, because the plausible future
    change is removing the swallow to make book-keeping failures observable.
    If the call has drifted above the post by then, a raising hook takes the
    post down with it and a reply the room never received is reported as sent —
    or, with the post wrapped alongside it, a reply that IS in the room gets
    announced as lost, the accident #302's own comments record.

    So this one patches `_record_errands` itself, bypassing the swallow and
    leaving only position under test. What it asserts is the consequence that
    actually hurts: the message is in the room, and the room grew no notice
    underneath it. Production cannot reach this — `_record_errands` never
    raises — which is why the raise is caught here rather than asserted away.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    from ._team_turn import speak_in_room

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )
    await db_client.insert(
        "agents", {"agent_id": LEAD, "agent_name": "L", "created_by": "usr_1"}
    )
    await db_client.insert(
        "bus_channel_members", {"channel_id": CHANNEL, "agent_id": LEAD}
    )

    async def _boom(*_a, **_k):
        raise RuntimeError("book-keeping blew up loudly")

    monkeypatch.setattr(
        "narranexus.platform.message_bus.team_posting._record_errands", _boom
    )

    bus = LocalMessageBus(backend=db_client._backend)
    try:
        await speak_in_room(
            db=db_client, bus=bus, agent_id=LEAD, team_id=TEAM,
            channel_id=CHANNEL, text="the answer", event_id="evt_1",
        )
    except RuntimeError:
        pass  # escaped the post rather than failing it — the distinction here

    rows = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    assert any(r["content"] == "the answer" for r in rows), "the reply was lost"
    # And no notice may appear underneath a reply that is sitting right there.
    assert not [r for r in rows if (r["msg_type"] or "") != "text"], (
        "a book-keeping failure was announced as a delivery failure"
    )


def test_the_team_prompt_names_the_alternatives_to_a_promise():
    """A bare ban would make silence the compliant answer.

    That is not hypothetical: the 0802 WeChat report came from a protocol whose
    own logic made saying nothing correct. So the rule has to carry the exits —
    finish now, or say how far you got, or schedule it — and the test asserts
    the exits, not just the prohibition.
    """
    from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
    from narranexus.platform.message_bus.schemas import BusMessage

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    msg = BusMessage(
        message_id="m1", channel_id=CHANNEL, from_agent="usr_u", content="status?"
    )
    text = trigger._build_team_prompt(
        LEAD, [msg],
        [{"agent_id": LEAD, "name": "Ana"}, {"agent_id": A3, "name": "A3"}],
        owner_user_id="usr_u", team_id=TEAM, trigger_messages=[msg],
        lead_agent_id=LEAD, work_items=[], bulletin=None,
    )

    assert "Do not promise future delivery" in text
    # The reason the promise cannot be kept — decoupled 2026-08-21 from the old
    # "sending this message ENDS your turn", which read as "one send and you're
    # done" and contradicted the cross-channel capability granted elsewhere. The
    # Dunhuang prohibition and its exits (below) are unchanged.
    assert "Nothing of yours keeps running once this turn ends" in text
    # The three exits.
    assert "finish the work in THIS turn" in text
    assert "how far you got" in text
    assert "job_create" in text


def test_the_group_im_protocol_carries_the_same_rule():
    """The 1:1 protocol has had this line since the DM work; the group one did
    not, so the same promise was compliant in every group channel."""
    from narranexus.platform.channel.channel_prompts import (
        COMMUNICATION_PROTOCOL_DIRECT,
        COMMUNICATION_PROTOCOL_GROUP,
    )

    assert "Do not promise future work" in COMMUNICATION_PROTOCOL_DIRECT
    assert "Do not promise future work" in COMMUNICATION_PROTOCOL_GROUP


def test_the_protocol_does_not_model_the_thing_it_forbids():
    """The rule and the worked example next to it have to agree.

    They did not: the "conversation is too frequent" bullet offered
    "Let me work on it and share results when ready" as the sentence to copy,
    one line under a new rule saying never to promise future work — and
    `is_promise_only` matches that sentence verbatim (`\\blet me (…|work on)\\b`).
    A model choosing between an abstract rule and a quoted example copies the
    example, and this fires in the one situation most likely to produce a
    promise.

    IM group channels have no work board behind them (`record_handoffs` runs on
    the team-room path only), so in those channels this prompt is the whole
    mechanism.

    Asserted with the repository's OWN definition rather than a substring
    match, so the two cannot drift apart again: whatever the example becomes,
    it has to pass the same classifier the feature runs on live messages.
    """
    import re

    from narranexus.platform.channel.channel_prompts import (
        COMMUNICATION_PROTOCOL_GROUP,
    )

    line = next(
        ln for ln in COMMUNICATION_PROTOCOL_GROUP.splitlines()
        if "For example:" in ln
    )
    example = re.search(r'For example: "([^"]+)"', line).group(1)

    assert is_promise_only(example) is False, (
        f"the protocol's worked example is itself a promise: {example!r}"
    )


# ===========================================================================
# The classifier, pinned directly
# ===========================================================================

def test_promise_detection_covers_both_languages_and_neither_direction_by_accident():
    """Kept narrow on purpose: this decides whether a guard stays armed.

    A false "promise" costs one patrol line; a false "delivery" costs the whole
    mechanism on exactly the message it was built for. So the bias is towards
    staying open, and the vocabulary is small enough to read in one screen.
    """
    for promise in (
        "收到，开始处理。……完成后产出 03_normalised.json 并交付 @A4",
        "好的，我稍后回来汇报",
        "let me look into it and get back to you",
        "I'll report back when it's ready",
        "收到",
        "on it",
    ):
        assert is_promise_only(promise) is True, promise

    for delivery in (
        "03_normalised.json is in the shared folder — 1,204 pages.",
        "找到 3 处错位，已在 notes.md 里逐条列出",
        "cannot do it: the scans for pages 40-60 are missing",
    ):
        assert is_promise_only(delivery) is False, delivery
