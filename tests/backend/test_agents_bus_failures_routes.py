"""
@file_name: test_agents_bus_failures_routes.py
@author: Bin Liang
@date: 2026-07-02
@description: Tests for the bus-failure recovery routes:
  GET  /api/agents/{agent_id}/bus-failures
  POST /api/agents/{agent_id}/bus-failures/{message_id}/retry

Upstream report: NetMindAI-Open/NarraNexus#52. `LocalMessageBus.get_pending_messages`
permanently filters out a message once its `bus_message_failures.retry_count`
reaches 3 (local_bus.py). These routes are the recovery path: list the
messages an owner's agent gave up on, and clear one so the next poll cycle
re-delivers it — same tenancy pattern as agents_cost.py (viewer resolved from
session, ownership enforced via `agents.created_by`, 404 masks both "no such
agent" and "not yours").
"""
from __future__ import annotations

import pytest_asyncio
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.schema_registry import auto_migrate

import backend.routes.agents_bus_failures as bus_failures_mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


def _build_client(db_client, viewer_id: str = "user_x"):
    app = FastAPI()
    app.include_router(bus_failures_mod.router, prefix="/api/agents")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        return await call_next(request)

    async def _get_db_override():
        return db_client

    import xyz_agent_context.utils.db_factory as db_factory_mod

    original = db_factory_mod.get_db_client
    db_factory_mod.get_db_client = _get_db_override
    bus_failures_mod.get_db_client = _get_db_override
    return TestClient(app), original


async def _seed_agent(db_client, agent_id="agent_a", owner="user_x"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


async def _seed_failure(
    db_client,
    message_id="m1",
    agent_id="agent_a",
    channel_id="ch1",
    retry_count=3,
    error="OpenAI API key invalid",
):
    await db_client.insert(
        "bus_messages",
        {
            "message_id": message_id,
            "channel_id": channel_id,
            "from_agent": "peer",
            "content": "hello",
            "created_at": "2026-07-01T00:00:00+00:00",
        },
    )
    await db_client.insert(
        "bus_message_failures",
        {
            "message_id": message_id,
            "agent_id": agent_id,
            "retry_count": retry_count,
            "last_error": error,
            "last_retry_at": "2026-07-01T00:05:00+00:00",
        },
    )


# ── GET list ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_failures_returns_owned_agent_failures(db_client):
    await _seed_agent(db_client)
    await _seed_failure(db_client)
    client, _ = _build_client(db_client)

    r = client.get("/api/agents/agent_a/bus-failures")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert len(body["failures"]) == 1
    failure = body["failures"][0]
    assert failure["message_id"] == "m1"
    assert failure["channel_id"] == "ch1"
    assert failure["retry_count"] == 3
    assert "API key" in failure["last_error"]


@pytest.mark.asyncio
async def test_list_failures_empty_for_agent_with_no_failures(db_client):
    await _seed_agent(db_client)
    client, _ = _build_client(db_client)

    r = client.get("/api/agents/agent_a/bus-failures")
    assert r.status_code == 200
    assert r.json()["failures"] == []


@pytest.mark.asyncio
async def test_list_failures_rejects_non_owner(db_client):
    """Agent owned by a different user must 404, not leak failure content."""
    await _seed_agent(db_client, owner="someone_else")
    await _seed_failure(db_client)
    client, _ = _build_client(db_client, viewer_id="user_x")

    r = client.get("/api/agents/agent_a/bus-failures")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_failures_rejects_unknown_agent(db_client):
    client, _ = _build_client(db_client)
    r = client.get("/api/agents/agent_ghost/bus-failures")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_failures_rejects_user_id_query_param(db_client):
    """Same TDR-12 defensive rejection as agents_cost.py — viewer identity
    only comes from the session, never a query param."""
    await _seed_agent(db_client)
    client, _ = _build_client(db_client)

    r = client.get("/api/agents/agent_a/bus-failures?user_id=someone_else")
    assert r.status_code == 400


# ── POST retry ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_clears_failure_record_and_lets_pending_pick_it_up(db_client):
    """After retry, the failure row is gone AND get_pending_messages (via
    LocalMessageBus) returns the message again — end-to-end recovery."""
    await _seed_agent(db_client)
    await _seed_failure(db_client)
    # Agent must be a channel member + have an unadvanced cursor for
    # get_pending_messages to surface the message at all.
    await db_client.insert(
        "bus_channel_members",
        {
            "channel_id": "ch1",
            "agent_id": "agent_a",
            "joined_at": "2026-06-30T00:00:00+00:00",
            "last_read_at": "2026-06-30T00:00:00+00:00",
            "last_processed_at": "2026-06-30T00:00:00+00:00",
        },
    )
    client, _ = _build_client(db_client)

    r = client.post("/api/agents/agent_a/bus-failures/m1/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["message_id"] == "m1"

    remaining = await db_client.get(
        "bus_message_failures", {"message_id": "m1", "agent_id": "agent_a"}
    )
    assert remaining == []

    from xyz_agent_context.message_bus.local_bus import LocalMessageBus

    bus = LocalMessageBus(backend=db_client._backend)
    pending = await bus.get_pending_messages("agent_a")
    assert any(m.message_id == "m1" for m in pending), (
        "retry must clear the poison filter so get_pending_messages "
        "picks the message back up"
    )


@pytest.mark.asyncio
async def test_retry_unknown_failure_returns_404(db_client):
    await _seed_agent(db_client)
    client, _ = _build_client(db_client)

    r = client.post("/api/agents/agent_a/bus-failures/m_nonexistent/retry")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_retry_rejects_non_owner(db_client):
    await _seed_agent(db_client, owner="someone_else")
    await _seed_failure(db_client)
    client, _ = _build_client(db_client, viewer_id="user_x")

    r = client.post("/api/agents/agent_a/bus-failures/m1/retry")
    assert r.status_code == 404

    # Must not have deleted the failure row of an agent the viewer doesn't own.
    remaining = await db_client.get(
        "bus_message_failures", {"message_id": "m1", "agent_id": "agent_a"}
    )
    assert len(remaining) == 1


@pytest.fixture(autouse=True)
def _restore_get_db():
    import xyz_agent_context.utils.db_factory as db_factory_mod

    original = db_factory_mod.get_db_client
    yield
    db_factory_mod.get_db_client = original
    bus_failures_mod.get_db_client = original
