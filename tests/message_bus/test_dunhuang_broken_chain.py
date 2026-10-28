"""
@file_name: test_dunhuang_broken_chain.py
@author:
@date: 2026-08-14
@description: End-to-end regression for the 2026-06-30 Dunhuang broken chain.

The founding incident: A3 answered a hand-off with "收到，开始处理……完成后交付
@A4", the run ended `completed` with that promise as its whole `final_output`,
and the six-stage pipeline died there in total silence. No error, no timeout,
no visible "this hand-off is still owed" anywhere.

The per-unit tests around this (`test_patrol_stall_detection`,
`test_patrol_candidates`, `test_patrol_turn`) each pin one link. What none of
them pin is the CHAIN: the whole point is that the detection, the cadence
switch, the candidate sweep and the prompt have to compose, because the
incident was a chain failure and any single working link would still have let
it happen.

Two residuals are pinned here too, deliberately as characterisation:

* a promise-shaped reply IS a delivery, so the undelivered-notice path (#296)
  cannot see the Dunhuang shape at all — the work board is the only mechanism
  that can;
* `events` carries a flat `root_run_id` tree LABEL and no parent pointer, so
  "which run caused this run" is still unanswerable (the acceptance criterion
  asking for the parent run is not satisfiable today).

If either becomes false, this file should be updated rather than deleted — a
change there is a real product change, not a test artefact.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.message_bus.patrol import (
    PATROL_INTERVAL_S,
    PATROL_MSG_TYPE,
    PATROL_STALLED_INTERVAL_S,
    detect_stalled_items,
    patrol_due_at,
    teams_due_for_patrol,
)
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemStatus
from narranexus.platform.utils.db.schema_registry import TABLES
from narranexus.platform.utils.timezone import utc_now


CHANNEL = "ch_dunhuang"
TEAM = "team_dunhuang"
LEAD = "agent_leader"
A3 = "agent_a3"
A4 = "agent_a4"

# The actual final_output of evt_41c581a8fe8a4bac, shortened.
PROMISE = "收到，开始处理。……完成后产出 03_normalised.json 并交付 @A4"


async def _seed_pipeline_room(db):
    """The room as it existed: a lead, the agent that promised, the next hop."""
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "Dunhuang Manuscript", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, name in ((LEAD, "Leader"), (A3, "A3"), (A4, "A4")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": name,
                                   "created_by": "usr_1"})
    await db.insert("teams", {
        "team_id": TEAM, "owner_user_id": "usr_1", "name": "Dunhuang",
        "lead_agent_id": LEAD,
    })


async def _went_quiet(db, agent_id, *, age_s=900):
    """The Dunhuang state: the promise was made, then nothing is running."""
    await db.insert("bus_agent_activity", {
        "agent_id": agent_id, "channel_id": CHANNEL, "state": "idle",
        "started_at": utc_now() - timedelta(seconds=age_s),
        "updated_at": utc_now() - timedelta(seconds=age_s),
    })


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )


# ===========================================================================
# The chain, in the order the incident walked it
# ===========================================================================

@pytest.mark.asyncio
async def test_the_promise_survives_the_run_that_made_it(db_client):
    """Link 1: the hand-off outlives A3's run.

    This is the whole premise. In the incident the task existed only inside
    A3's run, so when the run ended `completed` the task ceased to exist and
    there was nothing left for anyone to notice.
    """
    await _seed_pipeline_room(db_client)
    repo = TeamWorkItemRepository(db_client)

    item = await repo.create_item(
        team_id=TEAM, channel_id=CHANNEL, title="normalise the OCR output",
        created_by=LEAD, assignee_id=A3,
    )

    # A3's run has ended. Nothing about the item changed.
    reloaded = await repo.get(item.item_id)
    assert reloaded is not None, "the hand-off died with the run again"
    assert reloaded.status == WorkItemStatus.IN_PROGRESS
    assert reloaded.assignee_id == A3


@pytest.mark.asyncio
async def test_the_platform_and_not_the_model_calls_it_stalled(db_client):
    """Link 2: `stalled` is derived from activity, per iron rule #15.

    Asserted on the DB row, not just the return value: the prompt reads the
    board, so a verdict that is not written through is a verdict the lead
    never sees.
    """
    await _seed_pipeline_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    item = await repo.create_item(
        team_id=TEAM, channel_id=CHANNEL, title="normalise the OCR output",
        created_by=LEAD, assignee_id=A3,
    )
    await _went_quiet(db_client, A3)

    # Swept by the LEAD — A3 is the one under scrutiny.
    stalled = await detect_stalled_items(db_client, TEAM, executor_agent_id=LEAD)

    assert [i.item_id for i in stalled] == [item.item_id]
    assert (await repo.get(item.item_id)).status == WorkItemStatus.STALLED


@pytest.mark.asyncio
async def test_a_stall_tightens_the_patrol_cadence(db_client):
    """Link 3: a known stall is checked on the fast clock, not the slow one.

    Both directions, because a cadence that only ever tightens is a cadence
    that turns one broken team into a permanent fast-poller.
    """
    just_now = utc_now() - timedelta(seconds=PATROL_STALLED_INTERVAL_S + 5)

    assert patrol_due_at(just_now, True) is True
    assert patrol_due_at(just_now, False) is False, (
        "a healthy team was pulled onto the stalled cadence"
    )
    assert PATROL_STALLED_INTERVAL_S < PATROL_INTERVAL_S


@pytest.mark.asyncio
async def test_the_lead_is_woken_for_the_team_nobody_messaged(db_client):
    """Link 4: the sweep finds this team with no incoming message at all.

    The room is @-driven, so without this the lead simply never wakes and the
    stalled item sits there forever — which is exactly what happened.
    """
    await _seed_pipeline_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(
        team_id=TEAM, channel_id=CHANNEL, title="normalise the OCR output",
        created_by=LEAD, assignee_id=A3,
    )
    await _went_quiet(db_client, A3)

    due = await teams_due_for_patrol(db_client)

    assert (TEAM, LEAD, CHANNEL) in due


@pytest.mark.asyncio
async def test_the_broken_hand_off_becomes_a_visible_room_line(db_client):
    """Link 5, the payoff: the chain ends in something the USER can see.

    Everything above is invisible plumbing. The incident's actual cost was
    that the user could not tell "working" from "dead", so the test that
    matters is whether a line lands in the room — and lands as the PLATFORM,
    which is what keeps it out of the agent-hop count that the broken chain
    had already consumed.
    """
    await _seed_pipeline_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(
        team_id=TEAM, channel_id=CHANNEL, title="normalise the OCR output",
        created_by=LEAD, assignee_id=A3,
    )
    await _went_quiet(db_client, A3)

    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    seen: dict = {}

    async def _invoke(**kwargs):
        seen.update(kwargs)
        return TurnResult(text="@A3 status on 03_normalised.json?", event_id="evt_p")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, LEAD, CHANNEL)

    # Filters through the client rather than hand-written SQL: it builds
    # dialect-correct placeholders, and a hard-coded qmark is how bus delivery
    # broke on MySQL once before (2026-06-09).
    rows = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    posted = [r for r in (rows or []) if r["msg_type"] == PATROL_MSG_TYPE]
    assert len(posted) == 1, "the chase never reached the room"
    assert posted[0]["from_agent"] == f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}", (
        "posted as the lead — it would count as an agent hop"
    )

    # And the lead was told which item is stalled, by title.
    prompt = seen.get("prompt") or ""
    assert "normalise the OCR output" in prompt
    assert "STALLED" in prompt or "stalled" in prompt


# ===========================================================================
# Residuals — pinned as current behaviour, not as desired behaviour
# ===========================================================================

def test_a_promise_counts_as_a_delivery_so_the_notice_path_is_blind_to_it():
    """The undelivered notice (#296) covers ZERO delivery, not a false one.

    A3 did post text, so `reached_nobody` is False and no `system_undelivered`
    line is written. This is correct for what that mechanism promises and is
    precisely why it does not close the Dunhuang case: only the work board can.
    """
    promise_turn = TurnResult(text=PROMISE, event_id="evt_41c581a8fe8a4bac")

    assert promise_turn.reached_nobody is False, (
        "a promise now reads as silence — the notice path changed scope, "
        "re-check whether the work board is still the only Dunhuang guard"
    )
    # And the tool-side half of the same question agrees.
    assert MessageBusTrigger._delivered_to_anyone(["message_team"]) is True


def test_lineage_answers_which_tree_but_not_which_parent():
    """`root_run_id` is a flat inherited label by design (#252, cascade stop).

    So "give me the parent run of this run" — the acceptance criterion the PRD
    wrote for run lineage — has no column to read. Pinned at the schema so the
    gap is stated where someone would look for it.
    """
    names = {c.name for c in TABLES["events"].columns}

    assert "root_run_id" in names, "the tree label is gone — cascade stop relies on it"
    assert "parent_event_id" not in names
    assert not any("parent" in n for n in names), (
        "a parent pointer appeared — the PRD's lineage criterion may now be "
        "satisfiable; update section 四 of the PRD"
    )
