"""
@file_name: test_patrol_candidates.py
@author:
@date: 2026-08-10
@description: Which teams get patrolled, and how often.

The cost model lives here. Every patrol is a real LLM turn, so the candidate
query is what keeps the feature from quietly burning tokens on teams with
nothing to do — "empty board, zero runs" is a guarantee, not a nice-to-have.

Also pinned: the platform never appoints someone in charge. No lead means no
patrol, whatever is on the board.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from narranexus.platform.message_bus.patrol import (
    PATROL_INTERVAL_S,
    PATROL_STALLED_INTERVAL_S,
    patrol_due_at,
    teams_due_for_patrol,
)
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemStatus
from narranexus.platform.utils.timezone import utc_now


@pytest.fixture
def repo(db_client):
    return TeamWorkItemRepository(db_client)


async def _team(db, team_id="t1", *, lead="agent_lead", enabled=None, last=None):
    row = {
        "team_id": team_id, "owner_user_id": "usr_1", "name": team_id,
        "lead_agent_id": lead or None,
    }
    if enabled is not None:
        row["patrol_enabled"] = 1 if enabled else 0
    if last is not None:
        row["last_patrol_at"] = last
    await db.insert("teams", row)


# ── the cost guarantee ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_empty_board_produces_no_candidates(db_client, repo):
    """Zero items → zero patrol turns. The whole cost model rests on this."""
    await _team(db_client)
    await repo.create_item(team_id="t1", channel_id="ch_1", title="done",
                           created_by="agent_lead")
    items = await repo.list_active("t1")
    await repo.set_status(items[0].item_id, WorkItemStatus.DONE)

    assert await teams_due_for_patrol(db_client) == []


@pytest.mark.asyncio
async def test_a_board_with_work_is_a_candidate(db_client, repo):
    await _team(db_client)
    await repo.create_item(team_id="t1", channel_id="ch_room", title="OCR",
                           created_by="agent_lead")

    due = await teams_due_for_patrol(db_client)

    assert due == [("t1", "agent_lead", "ch_room")]


@pytest.mark.asyncio
async def test_a_paused_board_produces_no_candidates(db_client, repo):
    """A stopped tree must stop costing patrol turns, not just stop its runs."""
    await _team(db_client)
    await repo.create_item(team_id="t1", channel_id="ch_1", title="stopped",
                           created_by="agent_lead", root_run_id="evt_root")
    await repo.pause_by_root("evt_root")

    assert await teams_due_for_patrol(db_client) == []


# ── who is responsible ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_lead_means_no_patrol(db_client, repo):
    """The platform does not pick someone to be in charge on the user's behalf."""
    await _team(db_client, lead="")
    await repo.create_item(team_id="t1", channel_id="ch_1", title="OCR",
                           created_by="usr_1")

    assert await teams_due_for_patrol(db_client) == []


@pytest.mark.asyncio
async def test_patrol_can_be_switched_off(db_client, repo):
    await _team(db_client, enabled=False)
    await repo.create_item(team_id="t1", channel_id="ch_1", title="OCR",
                           created_by="agent_lead")

    assert await teams_due_for_patrol(db_client) == []


@pytest.mark.asyncio
async def test_undecided_reads_as_on_for_a_team_with_a_lead(db_client, repo):
    """NULL is the un-migrated default. Setting a lead IS the act of saying
    "this one is responsible", so patrol follows it rather than asking again."""
    await _team(db_client, enabled=None)
    await repo.create_item(team_id="t1", channel_id="ch_1", title="OCR",
                           created_by="agent_lead")

    assert len(await teams_due_for_patrol(db_client)) == 1


# ── adaptive pace ───────────────────────────────────────────────────────────

def test_a_never_patrolled_team_is_due():
    assert patrol_due_at(None, has_stalled=False) is True


def test_a_healthy_board_is_looked_at_rarely():
    just_now = utc_now() - timedelta(seconds=PATROL_INTERVAL_S - 60)
    assert patrol_due_at(just_now, has_stalled=False) is False

    long_ago = utc_now() - timedelta(seconds=PATROL_INTERVAL_S + 1)
    assert patrol_due_at(long_ago, has_stalled=False) is True


def test_a_stalled_board_is_looked_at_sooner():
    """The stalled window is when the flow is dead and nobody knows — the one
    period where interference is cheaper than silence."""
    recent = utc_now() - timedelta(seconds=PATROL_STALLED_INTERVAL_S + 1)

    assert patrol_due_at(recent, has_stalled=True) is True
    # Same instant, healthy board: not yet.
    assert patrol_due_at(recent, has_stalled=False) is False


@pytest.mark.asyncio
async def test_the_interval_is_honoured_end_to_end(db_client, repo):
    await _team(db_client, last=utc_now())
    await repo.create_item(team_id="t1", channel_id="ch_1", title="OCR",
                           created_by="agent_lead")

    assert await teams_due_for_patrol(db_client) == []


@pytest.mark.asyncio
async def test_one_broken_team_does_not_stop_the_sweep(db_client, repo):
    """A team row that cannot be read must not cost every other team its patrol."""
    await repo.create_item(team_id="t_ghost", channel_id="ch_1", title="orphan",
                           created_by="agent_lead")  # no teams row at all
    await _team(db_client, team_id="t_ok")
    await repo.create_item(team_id="t_ok", channel_id="ch_ok", title="OCR",
                           created_by="agent_lead")

    due = await teams_due_for_patrol(db_client)

    assert [d[0] for d in due] == ["t_ok"]
