"""
@file_name: test_agents_circuit_breaker_route.py
@author:
@date: 2026-07-13
@description: Tests for the manual circuit-breaker routes:
  GET  /api/agents/{agent_id}/circuit-breaker
  POST /api/agents/{agent_id}/circuit-breaker/reset

Same tenancy pattern as agents_bus_failures.py — viewer from session,
ownership via agents.created_by, 404 masks both "no such agent" and "not
yours".
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.agents_circuit_breaker as cb_mod
from xyz_agent_context.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from xyz_agent_context.schema import CbStatus, PausedReason
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.schema_registry import auto_migrate


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
    app.include_router(cb_mod.router, prefix="/api/agents")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        return await call_next(request)

    async def _get_db_override():
        return db_client

    import xyz_agent_context.utils.db_factory as db_factory_mod
    db_factory_mod.get_db_client = _get_db_override
    cb_mod.get_db_client = _get_db_override
    return TestClient(app)


async def _seed_agent(db_client, agent_id="agent_a", owner="user_x"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


@pytest.fixture(autouse=True)
def _restore_get_db():
    import xyz_agent_context.utils.db_factory as db_factory_mod
    original = db_factory_mod.get_db_client
    yield
    db_factory_mod.get_db_client = original
    cb_mod.get_db_client = original


# ── GET status ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_status_defaults_active_when_no_row(db_client):
    await _seed_agent(db_client)
    client = _build_client(db_client)
    r = client.get("/api/agents/agent_a/circuit-breaker")
    assert r.status_code == 200
    body = r.json()
    assert body["cb_status"] == "active"
    assert body["consecutive_failure_count"] == 0


@pytest.mark.asyncio
async def test_get_status_reports_paused(db_client):
    await _seed_agent(db_client)
    await AgentCircuitBreakerRepository(db_client).upsert_state(
        "agent_a",
        {"cb_status": CbStatus.PAUSED.value, "paused_reason": PausedReason.AUTH.value,
         "consecutive_failure_count": 3, "last_error": "401"},
    )
    client = _build_client(db_client)
    r = client.get("/api/agents/agent_a/circuit-breaker")
    body = r.json()
    assert body["cb_status"] == "paused"
    assert body["paused_reason"] == "auth"
    assert body["consecutive_failure_count"] == 3


# ── POST reset ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reset_clears_pause(db_client):
    await _seed_agent(db_client)
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("agent_a", {"cb_status": CbStatus.PAUSED.value,
                                        "paused_reason": PausedReason.AUTH.value,
                                        "consecutive_failure_count": 3})
    client = _build_client(db_client)
    r = client.post("/api/agents/agent_a/circuit-breaker/reset")
    assert r.status_code == 200
    assert r.json()["cb_status"] == "active"
    row = await repo.get("agent_a")
    assert row.cb_status == CbStatus.ACTIVE.value
    assert row.consecutive_failure_count == 0


@pytest.mark.asyncio
async def test_reset_rejects_non_owner(db_client):
    await _seed_agent(db_client, owner="someone_else")
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("agent_a", {"cb_status": CbStatus.PAUSED.value,
                                        "paused_reason": PausedReason.AUTH.value})
    client = _build_client(db_client, viewer_id="user_x")
    r = client.post("/api/agents/agent_a/circuit-breaker/reset")
    assert r.status_code == 404
    # Must not have reset an agent the viewer doesn't own.
    assert (await repo.get("agent_a")).cb_status == CbStatus.PAUSED.value


@pytest.mark.asyncio
async def test_get_rejects_unknown_agent(db_client):
    client = _build_client(db_client)
    r = client.get("/api/agents/ghost/circuit-breaker")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rejects_user_id_query_param(db_client):
    await _seed_agent(db_client)
    client = _build_client(db_client)
    r = client.get("/api/agents/agent_a/circuit-breaker?user_id=someone_else")
    assert r.status_code == 400
