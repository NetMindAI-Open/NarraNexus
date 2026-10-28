"""
@file_name: test_team_work_repository.py
@author:
@date: 2026-08-07
@description: The work board's data layer — what patrol and stop depend on.

Pinned here:
  * "does this team have anything unfinished" excludes paused and terminal
    states — patrol's whole cost model (zero items, zero runs) rests on it
  * a cascade stop pauses the items of ONE tree, and only the active ones
  * pausing is not cancelling: a paused item stays on the board and can be
    resumed, because a stop means "stop running", not "abandon the task"
  * claiming and completing move the state the way the prompts promise
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemStatus


@pytest.fixture
def repo(db_client):
    return TeamWorkItemRepository(db_client)


async def _item(repo, *, title="do the thing", status=WorkItemStatus.OPEN,
                team="team_1", root=None, assignee=None):
    item = await repo.create_item(
        team_id=team, channel_id="ch_1", title=title,
        created_by="agent_lead", root_run_id=root, assignee_id=assignee,
    )
    if status != WorkItemStatus.OPEN:
        await repo.set_status(item.item_id, status)
    return item


# ── patrol's candidate question ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unfinished_excludes_paused_and_terminal(repo):
    """`paused` must not count as unfinished, or a stop would not actually
    stop anything: patrol would keep seeing work and keep chasing it."""
    await _item(repo, title="open")
    await _item(repo, title="running", status=WorkItemStatus.IN_PROGRESS)
    await _item(repo, title="stuck", status=WorkItemStatus.STALLED)
    await _item(repo, title="paused", status=WorkItemStatus.PAUSED)
    await _item(repo, title="finished", status=WorkItemStatus.DONE)
    await _item(repo, title="dropped", status=WorkItemStatus.CANCELLED)

    active = await repo.list_active("team_1")

    assert {i.title for i in active} == {"open", "running", "stuck"}


@pytest.mark.asyncio
async def test_an_empty_board_reports_nothing(repo):
    """Zero items → zero patrol runs. This is the cost guarantee."""
    await _item(repo, title="finished", status=WorkItemStatus.DONE)

    assert await repo.list_active("team_1") == []
    assert await repo.teams_with_active_work() == []


@pytest.mark.asyncio
async def test_teams_with_active_work_is_scoped_per_team(repo):
    await _item(repo, team="team_1", title="live")
    await _item(repo, team="team_2", title="done", status=WorkItemStatus.DONE)

    assert await repo.teams_with_active_work() == ["team_1"]


@pytest.mark.asyncio
async def test_the_user_facing_list_keeps_paused_items(repo):
    """The board a HUMAN reads is not the board an agent reads.

    `list_active` hides `paused` so patrol stops chasing a stopped task. If the
    UI reused it, pressing stop would make the task vanish from the board and a
    stop would be indistinguishable from a delete — the exact thing the
    pause-not-cancel decision exists to avoid.
    """
    live = await _item(repo, title="live")
    parked = await _item(repo, title="parked", root="evt_root")
    await repo.pause_by_root("evt_root")
    await _item(repo, title="finished", status=WorkItemStatus.DONE)
    await _item(repo, title="dropped", status=WorkItemStatus.CANCELLED)
    await _item(repo, team="team_other", title="not mine")

    visible = await repo.list_visible("team_1")

    assert {i.title for i in visible} == {"live", "parked"}
    # Terminal states stay off it: the board is what still needs a decision.
    assert [i.item_id for i in visible] == [live.item_id, parked.item_id]


@pytest.mark.asyncio
async def test_the_user_facing_list_of_an_unknown_team_is_empty(repo):
    assert await repo.list_visible("team_nope") == []
    assert await repo.list_visible("") == []


# ── stop → pause ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stopping_a_tree_pauses_only_its_active_items(repo):
    mine_open = await _item(repo, title="mine-open", root="evt_root")
    mine_done = await _item(repo, title="mine-done", root="evt_root",
                            status=WorkItemStatus.DONE)
    other = await _item(repo, title="other-tree", root="evt_other")
    untracked = await _item(repo, title="no-tree", root=None)

    paused = await repo.pause_by_root("evt_root")

    assert paused == 1
    assert (await repo.get(mine_open.item_id)).status == WorkItemStatus.PAUSED
    # A finished item is not un-finished by a stop.
    assert (await repo.get(mine_done.item_id)).status == WorkItemStatus.DONE
    assert (await repo.get(other.item_id)).status == WorkItemStatus.OPEN
    # NULL root is not "the same tree" — otherwise one stop would freeze
    # every legacy item on the board.
    assert (await repo.get(untracked.item_id)).status == WorkItemStatus.OPEN


@pytest.mark.asyncio
async def test_pausing_an_unknown_tree_is_a_no_op(repo):
    await _item(repo, title="live", root="evt_root")
    assert await repo.pause_by_root("evt_nothing") == 0
    assert await repo.pause_by_root("") == 0


@pytest.mark.asyncio
async def test_a_paused_item_can_be_resumed(repo):
    """Pause is not cancel — the task survives, it just stops being chased."""
    item = await _item(repo, root="evt_root")
    await repo.pause_by_root("evt_root")

    await repo.set_status(item.item_id, WorkItemStatus.OPEN)

    assert (await repo.get(item.item_id)).status == WorkItemStatus.OPEN
    assert [i.item_id for i in await repo.list_active("team_1")] == [item.item_id]


# ── ordinary transitions ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claiming_sets_assignee_and_progress(repo):
    item = await _item(repo)

    await repo.claim(item.item_id, "agent_worker")

    got = await repo.get(item.item_id)
    assert got.assignee_id == "agent_worker"
    assert got.status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_completing_leaves_the_board(repo):
    item = await _item(repo, assignee="agent_worker")

    await repo.set_status(item.item_id, WorkItemStatus.DONE)

    assert await repo.list_active("team_1") == []
    # Still readable — a finished task is history, not a deletion.
    assert (await repo.get(item.item_id)).status == WorkItemStatus.DONE


@pytest.mark.asyncio
async def test_updating_an_unknown_item_does_not_raise(repo):
    """Tool calls arrive with model-supplied ids; a typo must degrade to a
    clean False, not an exception the agent then reports as a platform fault."""
    assert await repo.set_status("item_nope", WorkItemStatus.DONE) is False
    assert await repo.claim("item_nope", "agent_worker") is False
    assert await repo.get("item_nope") is None
