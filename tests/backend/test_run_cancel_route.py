"""
@file_name: test_run_cancel_route.py
@author:
@date: 2026-08-07
@description: POST /api/runs/{run_id}/cancel — the owner's stop request.

Confirms the acceptance criteria that live at this layer:
  * owner-only, enforced SERVER-side (403), independent of whether the
    frontend chose to render a button
  * the request is recorded as a timestamp the far-side watcher can see
  * repeated clicks are idempotent (one stop, not a queue of them)
  * a run that already settled is not re-flagged — a terminal row must not
    acquire a pending stop that a later run could inherit
  * ownership resolves through resolve_owner, NOT events.user_id (a team run
    stores the SENDER there, so trusting it would let any room participant
    stop somebody else's agent)
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.schema_registry import auto_migrate

import backend.routes.runs as runs_mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


def _build_client(db_client, monkeypatch, viewer_id: str = "user_owner"):
    app = FastAPI()
    app.include_router(runs_mod.router, prefix="/api/runs")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        return await call_next(request)

    async def _get_db_override():
        return db_client

    # monkeypatch, not a bare assignment: a permanent rewrite would leave every
    # later test in the session holding this closed in-memory client.
    monkeypatch.setattr(runs_mod, "get_db_client", _get_db_override)
    return TestClient(app)


async def _seed(
    db,
    *,
    run_id="evt_r1",
    agent_id="agent_a",
    owner="user_owner",
    state="running",
    triggering_user="usr_someone_else",
    root=None,
):
    if not await db.get_one("agents", {"agent_id": agent_id}):
        await db.insert(
            "agents",
            {
                "agent_id": agent_id,
                "agent_name": "A",
                "created_by": owner,
            },
        )
    await db.insert(
        "events",
        {
            "event_id": run_id,
            "trigger": "message_bus",
            "trigger_source": "message_bus",
            "agent_id": agent_id,
            # Deliberately NOT the owner — a team run stores the sender here.
            "user_id": triggering_user,
            "state": state,
            "started_at": "2026-08-07T10:00:00+00:00",
            "last_event_at": "2026-08-07T10:00:00+00:00",
            "root_run_id": root,
        },
    )


@pytest.mark.asyncio
async def test_owner_stop_records_the_request(db_client, monkeypatch):
    await _seed(db_client)
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    resp = client.post("/api/runs/evt_r1/cancel")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["already_settled"] is False
    row = await db_client.get_one("events", {"event_id": "evt_r1"})
    assert row["cancel_requested_at"]
    # The run itself is NOT terminated here — the watcher and the run's own
    # finalize own that. This endpoint only records intent.
    assert row["state"] == "running"


@pytest.mark.asyncio
async def test_non_owner_is_refused_by_the_server(db_client, monkeypatch):
    await _seed(db_client, owner="user_owner")
    client = _build_client(db_client, monkeypatch, viewer_id="user_intruder")

    resp = client.post("/api/runs/evt_r1/cancel")

    assert resp.status_code == 403
    row = await db_client.get_one("events", {"event_id": "evt_r1"})
    assert not row["cancel_requested_at"]


@pytest.mark.asyncio
async def test_triggering_user_is_not_the_owner(db_client, monkeypatch):
    """events.user_id is the run's triggering key, not ownership.

    In a team room it holds the SENDER. If the endpoint trusted it, every
    participant who ever addressed the agent could stop it.
    """
    await _seed(db_client, owner="user_owner", triggering_user="usr_sender")
    client = _build_client(db_client, monkeypatch, viewer_id="usr_sender")

    resp = client.post("/api/runs/evt_r1/cancel")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unknown_run_is_404(db_client, monkeypatch):
    client = _build_client(db_client, monkeypatch)
    assert client.post("/api/runs/evt_nope/cancel").status_code == 404


@pytest.mark.asyncio
async def test_repeated_clicks_are_idempotent(db_client, monkeypatch):
    await _seed(db_client)
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    first = client.post("/api/runs/evt_r1/cancel")
    row_after_first = await db_client.get_one("events", {"event_id": "evt_r1"})
    second = client.post("/api/runs/evt_r1/cancel")
    row_after_second = await db_client.get_one("events", {"event_id": "evt_r1"})

    assert first.status_code == 200
    assert second.status_code == 200
    # The first request's timestamp is what the watcher compares against
    # started_at; re-stamping it later would be harmless today but pointless,
    # and keeping it stable makes the audit trail read honestly.
    assert row_after_first["cancel_requested_at"] == row_after_second["cancel_requested_at"]


@pytest.mark.asyncio
async def test_settled_run_is_not_flagged(db_client, monkeypatch):
    """A finished run acquires no pending stop.

    The flag outlives the request, and the watcher's guard is
    `requested >= started_at`. Flagging a terminal row would leave a live
    trap for whatever run comes next on that agent.
    """
    await _seed(db_client, state="completed")
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    resp = client.post("/api/runs/evt_r1/cancel")

    assert resp.status_code == 200
    body = resp.json()
    assert body["already_settled"] is True
    row = await db_client.get_one("events", {"event_id": "evt_r1"})
    assert not row["cancel_requested_at"]


# ── cascade ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_whole_tree_is_stopped(db_client, monkeypatch):
    """One click stops every still-running run in the tree.

    The clicked run is a middle node on purpose: the owner clicks whatever the
    roster showed them, which is not necessarily the root, so selecting "the
    tree this run belongs to" must not depend on having clicked the root.
    """
    await _seed(db_client, run_id="evt_root", root="evt_root")
    await _seed(db_client, run_id="evt_child", root="evt_root")
    await _seed(db_client, run_id="evt_grandchild", root="evt_root")
    # A different tree must be untouched.
    await _seed(db_client, run_id="evt_other", root="evt_other")
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    resp = client.post("/api/runs/evt_child/cancel")

    assert resp.status_code == 200
    assert resp.json()["cascaded"] == 3
    for rid in ("evt_root", "evt_child", "evt_grandchild"):
        row = await db_client.get_one("events", {"event_id": rid})
        assert row["cancel_requested_at"], f"{rid} was left running"
    other = await db_client.get_one("events", {"event_id": "evt_other"})
    assert not other["cancel_requested_at"]


@pytest.mark.asyncio
async def test_settled_runs_in_the_tree_are_not_flagged(db_client, monkeypatch):
    """Only still-running rows are flagged — a finished sibling must not keep a
    pending stop that a future run could inherit."""
    await _seed(db_client, run_id="evt_root", root="evt_root")
    await _seed(db_client, run_id="evt_done", root="evt_root", state="completed")
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    resp = client.post("/api/runs/evt_root/cancel")

    assert resp.json()["cascaded"] == 1
    done = await db_client.get_one("events", {"event_id": "evt_done"})
    assert not done["cancel_requested_at"]


@pytest.mark.asyncio
async def test_an_unlabelled_run_stops_only_itself(db_client, monkeypatch):
    """A run from before the column exists has root_run_id NULL. Matching on
    NULL would select every other legacy row — so it degrades to a single stop.
    """
    await _seed(db_client, run_id="evt_legacy_a", root=None)
    await _seed(db_client, run_id="evt_legacy_b", root=None)
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    resp = client.post("/api/runs/evt_legacy_a/cancel")

    assert resp.json()["cascaded"] == 1
    b = await db_client.get_one("events", {"event_id": "evt_legacy_b"})
    assert not b["cancel_requested_at"]


# ── room trace ──────────────────────────────────────────────────────────────


async def _seed_room(db, channel_id="ch_team", agent_ids=("agent_a",), team=True):
    await db.insert(
        "bus_channels",
        {
            "channel_id": channel_id,
            "name": "room",
            "channel_type": "group",
            "created_by": "team_t1" if team else "agent_owner",
        },
    )
    for aid in agent_ids:
        await db.insert(
            "bus_channel_members", {"channel_id": channel_id, "agent_id": aid}
        )


async def _bind_activity(db, *, run_id, agent_id, channel_id="ch_team"):
    """The run→room mapping the trace walks (written by the bus trigger)."""
    await db.insert(
        "bus_agent_activity",
        {
            "agent_id": agent_id,
            "channel_id": channel_id,
            "state": "running",
            "event_id": run_id,
        },
    )


@pytest.mark.asyncio
async def test_every_stopped_agent_is_narrated(db_client, monkeypatch):
    """Three agents stopped by one click → three attributable system lines.

    The failure this pins: narrating only the clicked run leaves the other two
    vanishing from the room with no explanation — which is the exact confusion
    the trace exists to prevent, reintroduced by the cascade.
    """
    for i, aid in enumerate(("agent_a", "agent_b", "agent_c")):
        await _seed(db_client, run_id=f"evt_{i}", agent_id=aid, root="evt_0")
    await _seed_room(db_client, agent_ids=("agent_a", "agent_b", "agent_c"))
    for i, aid in enumerate(("agent_a", "agent_b", "agent_c")):
        await _bind_activity(db_client, run_id=f"evt_{i}", agent_id=aid)
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    resp = client.post("/api/runs/evt_1/cancel")

    assert resp.json()["cascaded"] == 3
    notices = await db_client.get("bus_messages", {"msg_type": "system_stop"})
    # Every stopped agent is traceable — a merged notice naming only one of
    # them would silently pass a "there is a notice" assertion.
    assert {n["from_agent"] for n in notices} == {"agent_a", "agent_b", "agent_c"}
    # And the notice must not be able to wake anyone it just stopped.
    assert all(not n["mentions"] for n in notices)


@pytest.mark.asyncio
async def test_a_peer_dm_gets_no_notice(db_client, monkeypatch):
    """A DM is not an audience — only team rooms are narrated."""
    await _seed(db_client, run_id="evt_dm", agent_id="agent_a", root="evt_dm")
    await _seed_room(db_client, channel_id="ch_dm", agent_ids=("agent_a",), team=False)
    await _bind_activity(db_client, run_id="evt_dm", agent_id="agent_a", channel_id="ch_dm")
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    client.post("/api/runs/evt_dm/cancel")

    assert await db_client.get("bus_messages", {"msg_type": "system_stop"}) == []


@pytest.mark.asyncio
async def test_a_chat_run_is_not_narrated(db_client, monkeypatch):
    """No activity row = the run never lived in a room; nothing to narrate."""
    await _seed(db_client, run_id="evt_chat", agent_id="agent_a", root="evt_chat")
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    resp = client.post("/api/runs/evt_chat/cancel")

    assert resp.status_code == 200
    assert await db_client.get("bus_messages", {"msg_type": "system_stop"}) == []


# ── work board linkage ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stopping_a_tree_parks_its_work_items(db_client, monkeypatch):
    """Stop must also park the board, or patrol undoes the stop.

    Ending the runs is not enough: the board still lists the task as
    unfinished, so the next patrol sees "unfinished item, assignee idle",
    chases it, and starts a fresh run — the owner presses stop and watches new
    work appear, this time initiated by the platform.
    """
    from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
    from narranexus.platform.schema.team_work_schema import WorkItemStatus

    await _seed(db_client, run_id="evt_root", root="evt_root")
    repo = TeamWorkItemRepository(db_client)
    mine = await repo.create_item(
        team_id="t1", channel_id="ch_team", title="the task",
        created_by="agent_lead", root_run_id="evt_root",
    )
    other = await repo.create_item(
        team_id="t1", channel_id="ch_team", title="unrelated",
        created_by="agent_lead", root_run_id="evt_other",
    )
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    client.post("/api/runs/evt_root/cancel")

    # Parked, not cancelled — a stop means "stop running", not "abandon it".
    assert (await repo.get(mine.item_id)).status == WorkItemStatus.PAUSED
    assert (await repo.get(other.item_id)).status == WorkItemStatus.OPEN
    # And patrol now sees nothing to chase for this tree.
    assert [i.item_id for i in await repo.list_active("t1")] == [other.item_id]


@pytest.mark.asyncio
async def test_a_stop_without_a_board_still_succeeds(db_client, monkeypatch):
    """The board is optional; a team that never used it must not break stop."""
    await _seed(db_client, run_id="evt_plain", root="evt_plain")
    client = _build_client(db_client, monkeypatch, viewer_id="user_owner")

    assert client.post("/api/runs/evt_plain/cancel").status_code == 200
