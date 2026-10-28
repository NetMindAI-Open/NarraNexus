"""
@file_name: test_work_board_transfer.py
@author:
@date: 2026-08-10
@description: The work board survives an export/import round trip.

A bundle that restored the room but not what it owes would look like a team
that had finished everything. So the board travels — but two of its columns
name things that do not exist in the target environment, and getting those
wrong is worse than dropping them.
"""
from __future__ import annotations

import pytest

from narranexus.platform.bundle.builder import _export_work_items
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemStatus


@pytest.fixture
def repo(db_client):
    return TeamWorkItemRepository(db_client)


@pytest.mark.asyncio
async def test_only_unfinished_work_travels(db_client, repo):
    """`done`/`cancelled` are history and belong with the chat log; `paused`
    ships because it is a live decision the recipient inherits."""
    await repo.create_item(team_id="t1", channel_id="ch", title="open",
                           created_by="agent_lead")
    running = await repo.create_item(team_id="t1", channel_id="ch", title="running",
                                     created_by="agent_lead", assignee_id="agent_a")
    parked = await repo.create_item(team_id="t1", channel_id="ch", title="parked",
                                    created_by="agent_lead", root_run_id="evt_r")
    await repo.pause_by_root("evt_r")
    finished = await repo.create_item(team_id="t1", channel_id="ch", title="finished",
                                      created_by="agent_lead")
    await repo.set_status(finished.item_id, WorkItemStatus.DONE)
    del running, parked

    exported = await _export_work_items(db_client, "t1")

    assert {i["title"] for i in exported} == {"open", "running", "parked"}


@pytest.mark.asyncio
async def test_the_source_run_tree_is_not_exported(db_client, repo):
    """`root_run_id` names a run in the SOURCE environment.

    Carrying it over would let a cascade stop in the target match items it
    never produced — a stop that silently parks somebody else's work.
    """
    await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                           created_by="agent_lead", root_run_id="evt_source")

    exported = await _export_work_items(db_client, "t1")

    assert "root_run_id" not in exported[0]


@pytest.mark.asyncio
async def test_assignee_is_carried_for_remapping(db_client, repo):
    """Kept as a SOURCE id — the importer remaps it with every other agent id,
    and clears it when the assignee fell outside the export closure."""
    await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                           created_by="agent_lead", assignee_id="agent_a")

    exported = await _export_work_items(db_client, "t1")

    assert exported[0]["assignee_id"] == "agent_a"


@pytest.mark.asyncio
async def test_a_team_with_no_board_exports_an_empty_list(db_client):
    assert await _export_work_items(db_client, "t_none") == []


@pytest.mark.asyncio
async def test_an_unreadable_board_is_not_an_export_failure(db_client):
    """A bundle is worth more than its board: an export must not die because
    one optional section could not be read."""
    class _Boom:
        async def get(self, *a, **kw):
            raise RuntimeError("table gone")

    assert await _export_work_items(_Boom(), "t1") == []
