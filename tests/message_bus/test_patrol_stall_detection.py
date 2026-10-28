"""
@file_name: test_patrol_stall_detection.py
@author:
@date: 2026-08-10
@description: Who decides an item is stalled — and on what evidence.

Iron rule #15: a correctness-critical fact must not depend on model obedience.
"Is this stalled" is derived here, from `bus_agent_activity`; the lead's
judgement applies only to what to DO about it.

The distinction that matters most is `running` vs `stalled`. A member that has
been thinking for 25 minutes is WORKING, and chasing it would be the platform
interrupting exactly the long-running turn iron rule #14 protects. Only a
member that is idle-with-unfinished-work, or whose heartbeat died, is stalled.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from narranexus.platform.message_bus.patrol import detect_stalled_items
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemStatus
from narranexus.platform.utils.timezone import utc_now


@pytest.fixture
def repo(db_client):
    return TeamWorkItemRepository(db_client)


async def _activity(db, agent_id, *, state, age_s=0, beat_s=None, channel_id="ch_1"):
    """Seed one activity row.

    ``age_s`` is how long ago the turn STARTED; ``beat_s`` how long ago it last
    beat (defaults to ``age_s``). The two are different facts and conflating
    them is the easy mistake: a turn running for 25 minutes has an old
    ``started_at`` and a FRESH ``updated_at`` — the heartbeat keeps ticking.
    Only a dead heartbeat means "we stopped hearing from it".
    """
    started = utc_now() - timedelta(seconds=age_s)
    beat = utc_now() - timedelta(seconds=age_s if beat_s is None else beat_s)
    await db.insert("bus_agent_activity", {
        "agent_id": agent_id, "channel_id": channel_id, "state": state,
        "started_at": started, "updated_at": beat,
    })


@pytest.mark.asyncio
async def test_a_long_running_member_is_not_stalled(db_client, repo):
    """25 minutes of thinking is WORK, not a stall.

    Chasing it would make the platform the interruption source for exactly the
    long-running turn iron rule #14 exists to protect.
    """
    item = await repo.create_item(
        team_id="t1", channel_id="ch_1", title="deep research",
        created_by="agent_lead", assignee_id="agent_a",
    )
    # Started 25 minutes ago and STILL BEATING — this is work in progress.
    await _activity(db_client, "agent_a", state="running", age_s=1500, beat_s=2)

    stalled = await detect_stalled_items(db_client, "t1", executor_agent_id="")

    assert stalled == []
    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_an_idle_assignee_with_unfinished_work_is_stalled(db_client, repo):
    """The Dunhuang shape: acknowledged, then went quiet, and nothing is running."""
    item = await repo.create_item(
        team_id="t1", channel_id="ch_1", title="OCR the scans",
        created_by="agent_lead", assignee_id="agent_a",
    )
    await _activity(db_client, "agent_a", state="idle", age_s=600)

    stalled = await detect_stalled_items(db_client, "t1", executor_agent_id="")

    assert [i.item_id for i in stalled] == [item.item_id]
    # Written through, so the patrol prompt reads a platform fact rather than
    # recomputing it — and so the UI can show the same word.
    assert (await repo.get(item.item_id)).status == WorkItemStatus.STALLED


@pytest.mark.asyncio
async def test_a_dead_heartbeat_is_stalled_even_though_it_says_running(db_client, repo):
    """`state='running'` with a stale heartbeat is a wedged worker, not work."""
    item = await repo.create_item(
        team_id="t1", channel_id="ch_1", title="OCR", created_by="agent_lead",
        assignee_id="agent_a",
    )
    await _activity(db_client, "agent_a", state="running", age_s=3600)

    stalled = await detect_stalled_items(db_client, "t1", executor_agent_id="")

    assert [i.item_id for i in stalled] == [item.item_id]


@pytest.mark.asyncio
async def test_unclaimed_work_is_not_stalled(db_client, repo):
    """Nobody is late on a task nobody took. It needs handing out, not chasing —
    a different prompt, so it must not be lumped in here."""
    await repo.create_item(
        team_id="t1", channel_id="ch_1", title="unclaimed", created_by="agent_lead",
    )

    assert await detect_stalled_items(db_client, "t1", executor_agent_id="") == []


@pytest.mark.asyncio
async def test_paused_items_are_never_stalled(db_client, repo):
    """A stopped tree's items must not re-enter patrol's attention by the back
    door — that would undo the stop through the stall path instead."""
    item = await repo.create_item(
        team_id="t1", channel_id="ch_1", title="stopped", created_by="agent_lead",
        assignee_id="agent_a", root_run_id="evt_root",
    )
    await repo.pause_by_root("evt_root")
    await _activity(db_client, "agent_a", state="idle", age_s=600)

    assert await detect_stalled_items(db_client, "t1", executor_agent_id="") == []
    assert (await repo.get(item.item_id)).status == WorkItemStatus.PAUSED


@pytest.mark.asyncio
async def test_an_assignee_with_no_activity_row_is_stalled(db_client, repo):
    """No row at all = never started. Same user-visible symptom as going quiet."""
    item = await repo.create_item(
        team_id="t1", channel_id="ch_1", title="OCR", created_by="agent_lead",
        assignee_id="agent_ghost",
    )

    stalled = await detect_stalled_items(db_client, "t1", executor_agent_id="")

    assert [i.item_id for i in stalled] == [item.item_id]


@pytest.mark.asyncio
async def test_recovery_clears_the_stall(db_client, repo):
    """A member that comes back must leave the stalled set, or patrol would
    keep chasing someone who is already working again."""
    item = await repo.create_item(
        team_id="t1", channel_id="ch_1", title="OCR", created_by="agent_lead",
        assignee_id="agent_a",
    )
    await _activity(db_client, "agent_a", state="idle", age_s=600)
    await detect_stalled_items(db_client, "t1", executor_agent_id="")
    assert (await repo.get(item.item_id)).status == WorkItemStatus.STALLED

    await db_client.update("bus_agent_activity", {"agent_id": "agent_a"},
                           {"state": "running", "updated_at": utc_now()})

    assert await detect_stalled_items(db_client, "t1", executor_agent_id="") == []
    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS


# ── which activity row ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_member_busy_in_another_room_is_still_stalled_here(db_client, repo):
    """The verdict must read THIS room's activity row, not any of the agent's.

    `bus_agent_activity` is keyed `(agent_id, channel_id)` and belonging to
    several teams is a supported shape — `_item_in_my_room` is built on it. So
    a cross-team member has several rows, and `get_one` is `LIMIT 1` with no
    `ORDER BY`: on MySQL the clustered PK makes that "whichever channel_id
    sorts first", which has nothing to do with the item.

    The failure it produces is the exact case this feature exists for: a member
    who has abandoned room B while happening to run a turn in room A is read as
    live, so B's item is never chased. Worse, the panel disagrees out loud —
    the roster is per-channel, so it shows the same agent idle in B while
    patrol treats it as busy.
    """
    item = await repo.create_item(
        team_id="t1", channel_id="ch_1", title="abandoned here",
        created_by="agent_lead", assignee_id="agent_a",
    )
    # Live in another room ("ch_0" sorts before "ch_1" — the row LIMIT 1 picks).
    await _activity(db_client, "agent_a", channel_id="ch_0",
                    state="running", age_s=10, beat_s=1)
    # Gone quiet in this one.
    await _activity(db_client, "agent_a", channel_id="ch_1",
                    state="idle", age_s=3600)

    stalled = await detect_stalled_items(db_client, "t1", executor_agent_id="")

    assert [i.item_id for i in stalled] == [item.item_id]
    assert (await repo.get(item.item_id)).status == WorkItemStatus.STALLED


@pytest.mark.asyncio
async def test_a_member_live_in_this_room_is_not_stalled(db_client, repo):
    """The mirror image: idle elsewhere must not condemn someone working here."""
    item = await repo.create_item(
        team_id="t1", channel_id="ch_1", title="in hand",
        created_by="agent_lead", assignee_id="agent_a",
    )
    await _activity(db_client, "agent_a", channel_id="ch_0", state="idle", age_s=3600)
    await _activity(db_client, "agent_a", channel_id="ch_1",
                    state="running", age_s=10, beat_s=1)

    assert await detect_stalled_items(db_client, "t1", executor_agent_id="") == []
    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS
