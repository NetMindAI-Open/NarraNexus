"""
@file_name: test_team_work_board_route.py
@author:
@date: 2026-08-10
@description: The user's half of the work board — see it, and un-park it.

Read-and-resume only: agents maintain the board through their tools, and a
board the user also edits by hand would drift from the one the lead is held
responsible for.

The load-bearing difference from the agent-facing view is `paused`. That is
the state a stop leaves behind, and it is the USER who decides whether to
resume — so hiding it (as the agent's list does) would make a stopped task
look deleted.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemStatus, WorkItemOrigin
from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.schema_registry import auto_migrate

import narranexus_plugins.teams.routes as teams_mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


def _client(db_client, monkeypatch, viewer="usr_owner"):
    app = FastAPI()
    app.include_router(teams_mod.router, prefix="/api/teams")

    @app.middleware("http")
    async def _auth(request, call_next):
        request.state.user_id = viewer
        return await call_next(request)

    async def _get_db():
        return db_client

    monkeypatch.setattr(teams_mod, "get_db_client", _get_db)
    return TestClient(app)


async def _seed(db, *, owner="usr_owner", lead="agent_lead"):
    await db.insert("teams", {
        "team_id": "t1", "owner_user_id": owner, "name": "Desk",
        "lead_agent_id": lead or None,
    })
    await db.insert("team_members", {"team_id": "t1", "agent_id": "agent_worker"})
    await db.insert("agents", {"agent_id": "agent_worker", "agent_name": "Bruno",
                               "created_by": owner})
    return TeamWorkItemRepository(db)


@pytest.mark.asyncio
async def test_the_board_shows_parked_items_too(db_client, monkeypatch):
    repo = await _seed(db_client)
    live = await repo.create_item(team_id="t1", channel_id="ch", title="live",
                                  created_by="agent_lead", assignee_id="agent_worker")
    parked = await repo.create_item(team_id="t1", channel_id="ch", title="parked",
                                    created_by="agent_lead", root_run_id="evt_r")
    await repo.pause_by_root("evt_r")
    done = await repo.create_item(team_id="t1", channel_id="ch", title="done",
                                  created_by="agent_lead")
    await repo.set_status(done.item_id, WorkItemStatus.DONE)

    r = _client(db_client, monkeypatch).get("/api/teams/t1/work-items")

    assert r.status_code == 200
    titles = {i["title"]: i["status"] for i in r.json()["items"]}
    # Parked is visible — a stopped task must not look deleted.
    assert titles == {"live": WorkItemStatus.IN_PROGRESS, "parked": WorkItemStatus.PAUSED}
    del live, parked
    # Assignee resolves to a display name so the panel need not join again.
    assert [i["assignee_name"] for i in r.json()["items"] if i["title"] == "live"] == ["Bruno"]


@pytest.mark.asyncio
async def test_resuming_returns_it_to_its_owner(db_client, monkeypatch):
    repo = await _seed(db_client)
    item = await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                                  created_by="agent_lead", assignee_id="agent_worker",
                                  root_run_id="evt_r")
    await repo.pause_by_root("evt_r")

    r = _client(db_client, monkeypatch).post(
        f"/api/teams/t1/work-items/{item.item_id}/resume"
    )

    assert r.status_code == 200
    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_resuming_an_unclaimed_item_returns_it_to_the_pool(db_client, monkeypatch):
    repo = await _seed(db_client)
    item = await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                                  created_by="agent_lead", root_run_id="evt_r")
    await repo.pause_by_root("evt_r")

    _client(db_client, monkeypatch).post(f"/api/teams/t1/work-items/{item.item_id}/resume")

    assert (await repo.get(item.item_id)).status == WorkItemStatus.OPEN


@pytest.mark.asyncio
async def test_resuming_something_already_live_is_not_an_error(db_client, monkeypatch):
    """The user clicked resume on a task that came back on its own."""
    repo = await _seed(db_client)
    item = await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                                  created_by="agent_lead")

    r = _client(db_client, monkeypatch).post(
        f"/api/teams/t1/work-items/{item.item_id}/resume"
    )

    assert r.status_code == 200
    assert (await repo.get(item.item_id)).status == WorkItemStatus.OPEN


@pytest.mark.asyncio
async def test_a_non_owner_sees_nothing(db_client, monkeypatch):
    repo = await _seed(db_client, owner="usr_owner")
    await repo.create_item(team_id="t1", channel_id="ch", title="secret",
                           created_by="agent_lead")

    r = _client(db_client, monkeypatch, viewer="usr_intruder").get("/api/teams/t1/work-items")

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patrol_can_be_switched_off_and_reported(db_client, monkeypatch):
    await _seed(db_client)
    client = _client(db_client, monkeypatch)

    # A team with a lead defaults to ON even though the column is NULL.
    assert client.get("/api/teams/t1/work-items").json()["patrol_enabled"] is True

    assert client.put("/api/teams/t1/patrol", json={"enabled": False}).status_code == 200
    assert client.get("/api/teams/t1/work-items").json()["patrol_enabled"] is False


@pytest.mark.asyncio
async def test_a_team_without_a_lead_reports_patrol_off(db_client, monkeypatch):
    """No lead = nobody responsible, so there is nothing to switch on."""
    await _seed(db_client, lead="")

    r = _client(db_client, monkeypatch).get("/api/teams/t1/work-items")

    assert r.json()["patrol_enabled"] is False


# ── auto errands collapse into one hand-off card ────────────────────────────
#
# A message that @mentions several agents opens one AUTO errand PER assignee
# (message_bus/errand.py). Each row reuses the SENDER's first line as its title,
# so rendering them one-per-row put the same sentence on the board once per
# recipient, attributed to people who did not say it. The board collapses one
# message's errands into a single hand-off card: sender → the people still owing
# a reply, and the misattributed sentence is dropped entirely.


async def _seed_room(db, *, owner="usr_owner", lead="agent_lead"):
    """Like `_seed`, but with the sender and two assignees all present as
    named members, because a hand-off card resolves the sender's name too."""
    await db.insert("teams", {
        "team_id": "t1", "owner_user_id": owner, "name": "Desk",
        "lead_agent_id": lead or None,
    })
    for agent_id, name in (("agent_lead", "Ada"), ("agent_b", "Bruno"), ("agent_c", "Cara")):
        await db.insert("team_members", {"team_id": "t1", "agent_id": agent_id})
        await db.insert("agents", {"agent_id": agent_id, "agent_name": name,
                                   "created_by": owner})
    return TeamWorkItemRepository(db)


@pytest.mark.asyncio
async def test_one_message_to_two_agents_is_one_handoff_card(db_client, monkeypatch):
    repo = await _seed_room(db_client)
    # One @message → two AUTO errands, same source message, same sender, each
    # row carrying the sender's own sentence as its title.
    for assignee in ("agent_b", "agent_c"):
        await repo.create_item(
            team_id="t1", channel_id="ch",
            title="我核完了公开数据，先给裁决",  # the sender's words, not the assignee's
            created_by="agent_lead", assignee_id=assignee,
            source_message_id="msg_x", origin=WorkItemOrigin.AUTO,
        )

    items = _client(db_client, monkeypatch).get("/api/teams/t1/work-items").json()["items"]

    # Collapsed to ONE card, not one-per-recipient.
    handoffs = [i for i in items if i.get("kind") == "handoff"]
    assert len(items) == 1 and len(handoffs) == 1
    card = handoffs[0]
    # The sender, and the people who still owe a reply — resolved to names.
    assert card["source_name"] == "Ada"
    assert set(card["assignee_names"]) == {"Bruno", "Cara"}
    # The misattributed sentence is gone: it is nowhere in the card.
    assert "我核完了公开数据" not in json.dumps(card, ensure_ascii=False)
    # Both underlying rows travel with the card so a paused group can resume.
    assert len(card["item_ids"]) == 2
    assert card["status"] == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_tool_task_stays_its_own_card(db_client, monkeypatch):
    """Explicit task-level items are untouched: title + assignee, one per row."""
    repo = await _seed_room(db_client)
    await repo.create_item(team_id="t1", channel_id="ch", title="Ship the deck",
                           created_by="agent_lead", assignee_id="agent_b",
                           origin=WorkItemOrigin.TOOL)

    items = _client(db_client, monkeypatch).get("/api/teams/t1/work-items").json()["items"]

    assert len(items) == 1
    assert items[0]["kind"] == "task"
    assert items[0]["title"] == "Ship the deck"
    assert items[0]["assignee_name"] == "Bruno"


@pytest.mark.asyncio
async def test_handoff_status_is_stalled_when_any_member_stalled(db_client, monkeypatch):
    """The platform can mark one recipient stalled; the card must surface it."""
    repo = await _seed_room(db_client)
    a = await repo.create_item(team_id="t1", channel_id="ch", title="ask",
                               created_by="agent_lead", assignee_id="agent_b",
                               source_message_id="msg_x", origin=WorkItemOrigin.AUTO)
    b = await repo.create_item(team_id="t1", channel_id="ch", title="ask",
                               created_by="agent_lead", assignee_id="agent_c",
                               source_message_id="msg_x", origin=WorkItemOrigin.AUTO)
    await repo.set_status(b.item_id, WorkItemStatus.STALLED)
    del a

    items = _client(db_client, monkeypatch).get("/api/teams/t1/work-items").json()["items"]

    assert len(items) == 1
    assert items[0]["status"] == WorkItemStatus.STALLED


@pytest.mark.asyncio
async def test_a_users_own_handoff_shows_a_name_not_a_raw_id(db_client, monkeypatch):
    """The most common multi-@ path is a person naming agents, so the sender is
    `usr_<id>` — which `name_by_agent` (agents only) cannot resolve. The board
    must render the viewer's display name, never the opaque id."""
    repo = await _seed_room(db_client)
    await db_client.insert("users", {
        "user_id": "usr_owner", "display_name": "Ada Owner", "user_type": "local",
    })
    await repo.create_item(
        team_id="t1", channel_id="ch", title="@Bruno pull the numbers",
        created_by="usr_owner", assignee_id="agent_b",
        source_message_id="msg_u", origin=WorkItemOrigin.AUTO,
    )

    items = _client(db_client, monkeypatch).get("/api/teams/t1/work-items").json()["items"]

    assert len(items) == 1
    assert items[0]["source_name"] == "Ada Owner"
    assert "usr_" not in json.dumps(items[0], ensure_ascii=False)


@pytest.mark.asyncio
async def test_a_paused_row_in_a_handoff_stays_reachable(db_client, monkeypatch):
    """Half-paused group: the card aggregates to in_progress, but the paused
    row must still be exposed so its resume affordance does not vanish."""
    repo = await _seed_room(db_client)
    live = await repo.create_item(team_id="t1", channel_id="ch", title="ask",
                                  created_by="agent_lead", assignee_id="agent_b",
                                  source_message_id="msg_x", origin=WorkItemOrigin.AUTO)
    parked = await repo.create_item(team_id="t1", channel_id="ch", title="ask",
                                    created_by="agent_lead", assignee_id="agent_c",
                                    source_message_id="msg_x", origin=WorkItemOrigin.AUTO)
    await repo.set_status(parked.item_id, WorkItemStatus.PAUSED)

    items = _client(db_client, monkeypatch).get("/api/teams/t1/work-items").json()["items"]

    assert len(items) == 1
    # One row still active, so the card as a whole is in progress...
    assert items[0]["status"] == WorkItemStatus.IN_PROGRESS
    # ...but the parked row is called out so the panel can still resume it.
    assert items[0]["paused_item_ids"] == [parked.item_id]
    assert live.item_id not in items[0]["paused_item_ids"]


@pytest.mark.asyncio
async def test_two_different_messages_stay_two_cards(db_client, monkeypatch):
    """Collapse is per message — a genuine second hand-off is its own card."""
    repo = await _seed_room(db_client)
    await repo.create_item(team_id="t1", channel_id="ch", title="ask 1",
                           created_by="agent_lead", assignee_id="agent_b",
                           source_message_id="msg_1", origin=WorkItemOrigin.AUTO)
    await repo.create_item(team_id="t1", channel_id="ch", title="ask 2",
                           created_by="agent_lead", assignee_id="agent_b",
                           source_message_id="msg_2", origin=WorkItemOrigin.AUTO)

    items = _client(db_client, monkeypatch).get("/api/teams/t1/work-items").json()["items"]

    assert len([i for i in items if i.get("kind") == "handoff"]) == 2


# ── clear team data ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clearing_chat_leaves_the_board_alone(db_client, monkeypatch):
    """The two answer different questions.

    Chat is what was said; the board is what is still owed. Someone clearing a
    noisy transcript almost never means "and forget what we agreed to do".
    """
    repo = await _seed(db_client)
    item = await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                                  created_by="agent_lead")

    r = _client(db_client, monkeypatch).delete(
        "/api/teams/t1/data?chat=true&files=false"
    )

    assert r.status_code == 200
    assert r.json()["work_items"] == 0
    assert await repo.get(item.item_id) is not None


@pytest.mark.asyncio
async def test_the_board_can_be_cleared_on_its_own(db_client, monkeypatch):
    """And the reverse: abandoning the work should not require wiping history."""
    repo = await _seed(db_client)
    item = await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                                  created_by="agent_lead")

    r = _client(db_client, monkeypatch).delete(
        "/api/teams/t1/data?chat=false&files=false&board=true"
    )

    assert r.status_code == 200
    assert r.json()["work_items"] == 1
    assert await repo.get(item.item_id) is None


@pytest.mark.asyncio
async def test_no_scope_at_all_is_rejected(db_client, monkeypatch):
    await _seed(db_client)

    r = _client(db_client, monkeypatch).delete(
        "/api/teams/t1/data?chat=false&files=false&board=false"
    )

    assert r.status_code == 400
