"""
@file_name: test_work_board_tools.py
@author:
@date: 2026-08-07
@description: The work-board MCP tools — what a Leader can and cannot write.

The board is the first task-level object agents maintain themselves, so the
tools carry the boundary between "what the model decides" and "what the
platform decides":

  * a model may open, claim and finish items
  * a model may NOT write `stalled` (platform-derived from bus_agent_activity
    + errand timeouts — iron rule #15) nor `paused` (what a stop leaves
    behind) nor `cancelled` (the user's call)
  * the tools degrade to a clean `success: False` on unknown ids, because item
    ids arrive from model-authored calls and an exception would be reported to
    the user as a platform fault
"""
from __future__ import annotations

from typing import Any, Callable, Dict

import pytest

from narranexus_plugins.message_bus_module import _work_board_mcp_tools as mod
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemStatus


class _FakeMCP:
    """Collects the registered tools so tests can call them directly."""

    def __init__(self) -> None:
        self.tools: Dict[str, Callable] = {}

    def tool(self, *a: Any, **kw: Any):
        def deco(fn: Callable) -> Callable:
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def tools(db_client, monkeypatch):
    async def _get_db():
        return db_client

    async def _room(db, agent_id):
        return ("t1", "ch_1")

    monkeypatch.setattr(mod, "_get_db", _get_db)
    # The room a work item belongs to is resolved from live activity; tests pin
    # it so the tool's own logic is what's under test.
    monkeypatch.setattr(mod, "_resolve_team_room", _room)
    monkeypatch.setattr(mod, "caller_root_run_id", lambda: "evt_root")
    mcp = _FakeMCP()
    mod.register_work_board_mcp_tools(mcp)
    return mcp.tools


@pytest.fixture
def repo(db_client):
    return TeamWorkItemRepository(db_client)


# ── creating and moving ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_creating_an_item_lands_on_the_board(tools, repo):
    r = await tools["team_work_add"]("agent_lead", "write the OCR report")

    assert r["success"] is True
    items = await repo.list_active("t1")
    assert [i.title for i in items] == ["write the OCR report"]
    # The tree is stamped so a later stop can park it (see #252 + stop→pause).
    assert items[0].root_run_id == "evt_root"
    assert items[0].status == WorkItemStatus.OPEN


@pytest.mark.asyncio
async def test_assigning_at_creation_starts_it(tools, repo):
    await tools["team_work_add"]("agent_lead", "OCR", assignee_id="agent_a")

    item = (await repo.list_active("t1"))[0]
    assert item.assignee_id == "agent_a"
    assert item.status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_claiming_and_finishing(tools, repo):
    created = await tools["team_work_add"]("agent_lead", "OCR")
    item_id = created["item_id"]

    assert (await tools["team_work_claim"]("agent_a", item_id))["success"] is True
    assert (await repo.get(item_id)).assignee_id == "agent_a"

    assert (await tools["team_work_complete"]("agent_a", item_id))["success"] is True
    assert (await repo.get(item_id)).status == WorkItemStatus.DONE
    assert await repo.list_active("t1") == []


@pytest.mark.asyncio
async def test_listing_shows_the_board(tools, repo):
    await tools["team_work_add"]("agent_lead", "one")
    await tools["team_work_add"]("agent_lead", "two", assignee_id="agent_a")

    r = await tools["team_work_list"]("agent_lead")

    assert r["success"] is True
    assert {i["title"] for i in r["items"]} == {"one", "two"}


# ── the platform/model boundary ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_model_cannot_declare_an_item_stalled(tools, repo):
    """`stalled` is derived from platform data, never asserted by a model.

    If a model could set it, the patrol prompt's "these are stalled" section
    would be reporting the model's own guess back to it — and iron rule #15
    forbids putting a correctness-critical fact on model obedience.
    """
    created = await tools["team_work_add"]("agent_lead", "OCR")

    r = await tools["team_work_update_status"](
        "agent_lead", created["item_id"], WorkItemStatus.STALLED
    )

    assert r["success"] is False
    assert "stalled" in r["error"].lower()
    assert (await repo.get(created["item_id"])).status == WorkItemStatus.OPEN


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden", [WorkItemStatus.PAUSED, WorkItemStatus.CANCELLED])
async def test_a_model_cannot_pause_or_cancel(tools, repo, forbidden):
    """`paused` belongs to the stop path, `cancelled` to the user.

    An agent that could pause its own work board would be able to silence
    patrol — the exact supervision this feature exists to add.
    """
    created = await tools["team_work_add"]("agent_lead", "OCR")

    r = await tools["team_work_update_status"]("agent_lead", created["item_id"], forbidden)

    assert r["success"] is False
    assert (await repo.get(created["item_id"])).status == WorkItemStatus.OPEN


@pytest.mark.asyncio
async def test_an_unknown_status_is_refused(tools, repo):
    created = await tools["team_work_add"]("agent_lead", "OCR")

    r = await tools["team_work_update_status"]("agent_lead", created["item_id"], "whatever")

    assert r["success"] is False
    assert (await repo.get(created["item_id"])).status == WorkItemStatus.OPEN


# ── degradation ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_item_ids_degrade_cleanly(tools):
    """A model typo must read as "not found", not as a platform fault."""
    for call in (
        tools["team_work_claim"]("agent_a", "wi_nope"),
        tools["team_work_complete"]("agent_a", "wi_nope"),
        tools["team_work_update_status"]("agent_a", "wi_nope", WorkItemStatus.DONE),
    ):
        r = await call
        assert r["success"] is False
        assert "not found" in r["error"].lower()


@pytest.mark.asyncio
async def test_outside_a_team_room_the_board_is_unavailable(db_client, monkeypatch):
    """Work items are a TEAM object; a peer DM or owner chat has no board.

    Degrades with a reason rather than inventing a team, so the agent can say
    something true instead of writing an item nobody will ever see.
    """
    async def _get_db():
        return db_client

    async def _no_room(db, agent_id):
        return (None, None)

    monkeypatch.setattr(mod, "_get_db", _get_db)
    monkeypatch.setattr(mod, "_resolve_team_room", _no_room)
    monkeypatch.setattr(mod, "caller_root_run_id", lambda: None)
    mcp = _FakeMCP()
    mod.register_work_board_mcp_tools(mcp)

    r = await mcp.tools["team_work_add"]("agent_lead", "OCR")

    assert r["success"] is False
    assert "team" in r["error"].lower()


# ── cross-team scope ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_tools_refuse_another_teams_item(db_client, monkeypatch):
    """An item id alone must not be a write capability.

    Reachable without an attacker: an agent can belong to several teams, and
    the prompt's work-board section prints `id=` for every item — so last
    turn's board from team A sits in the context while this turn runs in team
    B. The board is the only evidence of who gets chased, so a cross-team write
    silences the wrong person.
    """
    other = await TeamWorkItemRepository(db_client).create_item(
        team_id="t_other", channel_id="ch_other", title="not yours",
        created_by="agent_lead", assignee_id="agent_x",
    )

    async def _get_db():
        return db_client

    async def _room(db, agent_id):
        return ("t1", "ch_1")  # this turn runs in t1, the item lives in t_other

    monkeypatch.setattr(mod, "_get_db", _get_db)
    monkeypatch.setattr(mod, "_resolve_team_room", _room)
    monkeypatch.setattr(mod, "caller_root_run_id", lambda: "evt_root")
    mcp = _FakeMCP()
    mod.register_work_board_mcp_tools(mcp)

    for call in (
        mcp.tools["team_work_claim"]("agent_a", other.item_id),
        mcp.tools["team_work_complete"]("agent_a", other.item_id),
        mcp.tools["team_work_update_status"]("agent_a", other.item_id, WorkItemStatus.DONE),
    ):
        r = await call
        assert r["success"] is False
        # "not found", never "exists but is not yours" — the latter would leak
        # the other team's ids straight back into this context.
        assert "not found" in r["error"].lower()

    # And the other team's board is untouched.
    assert (await TeamWorkItemRepository(db_client).get(other.item_id)).status == (
        WorkItemStatus.IN_PROGRESS
    )
