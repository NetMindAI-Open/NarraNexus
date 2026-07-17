"""
@file_name: test_agents_clear_history_route.py
@author: NarraNexus
@date: 2026-07-10
@description: Tests for DELETE /api/agents/{agent_id}/history — the scoped
"clear conversation & memory" endpoint.

Confirms: scope query params drive the wipe; at least one scope is required
(400 otherwise); owner-only (404 for non-owner, memory_* is agent-scoped so
this is a hard security boundary); `?user_id=` rejected (TDR-12).
"""
from __future__ import annotations

import json

import pytest_asyncio
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.schema_registry import auto_migrate

import backend.routes.agents_chat_history as hist_mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


def _build_client(db_client, viewer_id: str = "user_x", tmp_path=None):
    app = FastAPI()
    app.include_router(hist_mod.router, prefix="/api/agents")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        return await call_next(request)

    async def _get_db_override():
        return db_client

    import xyz_agent_context.utils.db_factory as db_factory_mod

    db_factory_mod.get_db_client = _get_db_override
    hist_mod.get_db_client = _get_db_override
    return TestClient(app)


async def _seed(db, agent_id="agent_a", owner="user_x"):
    await db.insert("agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner})
    await db.insert("narratives", {
        "narrative_id": "N1", "type": "other", "agent_id": agent_id,
        "narrative_info": json.dumps({"actors": [{"id": owner}]}),
    })
    await db.insert("events", {
        "event_id": "e1", "trigger": "chat", "trigger_source": "chat",
        "narrative_id": "N1", "agent_id": agent_id, "user_id": owner,
    })
    await db.insert("memory_entity", {
        "record_id": "r1", "agent_id": agent_id, "scope_type": "agent", "kind": "entity",
    })


@pytest.fixture(autouse=True)
def _restore_get_db():
    import xyz_agent_context.utils.db_factory as db_factory_mod
    original = db_factory_mod.get_db_client
    yield
    db_factory_mod.get_db_client = original
    hist_mod.get_db_client = original


@pytest.mark.asyncio
async def test_full_wipe_default_scopes(db_client):
    await _seed(db_client)
    client = _build_client(db_client)

    r = client.request("DELETE", "/api/agents/agent_a/history?conversations=true&memory=true")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["narratives_count"] == 1
    assert body["memory_rows_count"] >= 1
    assert await db_client.get("narratives", {"agent_id": "agent_a"}) == []
    assert await db_client.get("memory_entity", {"agent_id": "agent_a"}) == []


@pytest.mark.asyncio
async def test_conversations_only_keeps_memory(db_client):
    await _seed(db_client)
    client = _build_client(db_client)

    r = client.request("DELETE", "/api/agents/agent_a/history?conversations=true&memory=false")
    assert r.status_code == 200
    assert await db_client.get("narratives", {"agent_id": "agent_a"}) == []
    # memory kept
    assert len(await db_client.get("memory_entity", {"agent_id": "agent_a"})) == 1


@pytest.mark.asyncio
async def test_no_scope_selected_is_400(db_client):
    await _seed(db_client)
    client = _build_client(db_client)
    r = client.request("DELETE", "/api/agents/agent_a/history?conversations=false&memory=false")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_non_owner_is_404_and_no_deletion(db_client):
    await _seed(db_client, owner="someone_else")
    client = _build_client(db_client, viewer_id="user_x")
    r = client.request("DELETE", "/api/agents/agent_a/history?conversations=true&memory=true")
    assert r.status_code == 404
    # nothing deleted
    assert len(await db_client.get("narratives", {"agent_id": "agent_a"})) == 1
    assert len(await db_client.get("memory_entity", {"agent_id": "agent_a"})) == 1


@pytest.mark.asyncio
async def test_rejects_user_id_query_param(db_client):
    await _seed(db_client)
    client = _build_client(db_client)
    r = client.request("DELETE", "/api/agents/agent_a/history?user_id=someone_else")
    assert r.status_code == 400
