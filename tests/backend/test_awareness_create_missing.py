"""
@file_name: test_awareness_create_missing.py
@author:
@date: 2026-08-10
@description: The PUT awareness route's create_missing switch — the parity
half the MCP data-access seam (HttpStore) relies on.

The route's convenience default auto-creates an AwarenessModule instance for
any agent_id (the frontend contract). With ``create_missing=false`` an unknown
agent is an ERROR (200 + success:false, the routes' failure shape) and nothing
is created — matching DirectStore, where the LLM-supplied agent_id must never
mint instances for arbitrary ids.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus_plugins.awareness_module import routes as aw


@pytest.fixture
def client(monkeypatch):
    created: list[str] = []
    upserts: list[tuple[str, str]] = []
    known = {"agent_known": "aware_1"}

    async def fake_find(agent_id):
        return known.get(agent_id)

    async def fake_ensure(agent_id):
        iid = known.get(agent_id)
        if iid:
            return iid
        created.append(agent_id)
        known[agent_id] = f"aware_new_{agent_id}"
        return known[agent_id]

    async def fake_get_db_client():
        class _Db:
            async def get_one(self, table, filters):
                return None

        return _Db()

    class FakeAwarenessRepo:
        def __init__(self, db):
            pass

        async def get_by_instance(self, instance_id):
            # The route READS before it writes now: the model rewrites the whole
            # profile here and the prescribed format omits the platform identity
            # record, so the stored copy is needed to carry that section over.
            # None = nothing stored yet, which is this test's shape.
            return None

        async def upsert(self, instance_id, awareness):
            upserts.append((instance_id, awareness))
            return True

    monkeypatch.setattr(aw, "_find_awareness_instance", fake_find)
    monkeypatch.setattr(aw, "_ensure_awareness_instance", fake_ensure)
    monkeypatch.setattr(aw, "get_db_client", fake_get_db_client)
    monkeypatch.setattr(aw, "InstanceAwarenessRepository", FakeAwarenessRepo)

    app = FastAPI()
    app.include_router(aw.router, prefix="/api/agents")
    c = TestClient(app)
    c.created = created  # type: ignore[attr-defined]
    c.upserts = upserts  # type: ignore[attr-defined]
    return c


def test_default_still_auto_creates(client):
    r = client.put("/api/agents/agent_new/awareness", json={"awareness": "x"})
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert client.created == ["agent_new"]


def test_create_missing_false_errors_without_creating(client):
    r = client.put(
        "/api/agents/agent_new/awareness",
        params={"create_missing": "false"},
        json={"awareness": "x"},
    )
    assert r.status_code == 200  # the routes' failure shape is 200+success:false
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "No AwarenessModule instance found for agent_id=agent_new"
    assert client.created == []
    assert client.upserts == []


def test_get_does_not_auto_create(client):
    # A read must never mint an instance (security audit P0-1): GET on an agent
    # with no AwarenessModule instance returns not-found, it does not create one.
    r = client.get("/api/agents/agent_unknown/awareness")
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert client.created == []


def test_create_missing_false_updates_existing(client):
    r = client.put(
        "/api/agents/agent_known/awareness",
        params={"create_missing": "false"},
        json={"awareness": "hello"},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert client.upserts == [("aware_1", "hello")]
    assert client.created == []
