"""
@file_name: test_provider_slots_bulk.py
@author:
@date: 2026-08-26
@description: Owner-scoped bulk slot endpoints — override-stats / apply-to-agents.
    Uses an in-memory _FakeDB (equality-filter store) wired
    via monkeypatching providers.get_db_client, plus a fake auth middleware that
    lifts X-User-Id into request.state (same shape auth_middleware guarantees).
"""

import pytest

from tests.fake_db import FakeDB
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod

OWNER = {"X-User-Id": "owner1"}


_FakeDB = FakeDB
@pytest.fixture
def db():
    return _FakeDB()


@pytest.fixture
def client(monkeypatch, db):
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        role = request.headers.get("X-Role")
        if role:
            request.state.role = role
        return await call_next(request)

    async def _get_db():
        return db
    # Patch the SOURCE symbol (the endpoints import get_db_client locally at
    # call time) — the codebase-wide pattern, shared with the ~10 other tests
    # that source-patch db_factory.get_db_client.
    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )

    app.include_router(providers_mod.router, prefix="/api/providers")
    return TestClient(app, raise_server_exceptions=False)


async def _seed(db, agent_id, owner, slot=None, model="m"):
    await db.insert("agents", {"agent_id": agent_id, "created_by": owner, "name": agent_id})
    if slot:
        await db.insert("agent_slots", {
            "agent_id": agent_id, "slot_name": slot, "provider_id": "p1",
            "model": model, "params_json": "{}",
            "created_at": "2026-08-26T00:00:00+00:00",
            "updated_at": "2026-08-26T00:00:00+00:00"})


@pytest.mark.asyncio
async def test_override_stats(client, db):
    await _seed(db, "a1", "owner1", slot="agent")
    await _seed(db, "a2", "owner1")            # inherits
    await _seed(db, "b1", "owner2", slot="agent")  # other owner
    r = client.get("/api/providers/slots/override-stats", headers=OWNER)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data == {"agent": 1, "helper_llm": 0, "total_agents": 2}


@pytest.mark.asyncio
async def test_apply_to_agents_clears_selected_slot(client, db):
    await _seed(db, "a1", "owner1", slot="agent")
    await _seed(db, "a2", "owner1", slot="agent")
    await _seed(db, "a1b", "owner1", slot="helper_llm")  # different slot untouched
    r = client.post("/api/providers/slots/apply-to-agents",
                    json={"slots": ["agent"]}, headers=OWNER)
    assert r.status_code == 200
    assert r.json()["data"]["cleared"] == {"agent": 2}
    assert await db.get_one("agent_slots", {"agent_id": "a1", "slot_name": "agent"}) is None
    assert await db.get_one("agent_slots", {"agent_id": "a1b", "slot_name": "helper_llm"}) is not None


@pytest.mark.asyncio
async def test_apply_to_agents_rejects_bad_slot(client, db):
    r = client.post("/api/providers/slots/apply-to-agents",
                    json={"slots": ["bogus"]}, headers=OWNER)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_apply_to_agents_caps_slots_length(client, db):
    # slots list is capped at len(SlotName) via Pydantic — an inflated body is
    # rejected with 422 before any DB work (C1 amplification guard).
    r = client.post("/api/providers/slots/apply-to-agents",
                    json={"slots": ["agent"] * 50}, headers=OWNER)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_apply_to_agents_dedups_slots(client, db):
    await _seed(db, "a1", "owner1", slot="agent")
    r = client.post("/api/providers/slots/apply-to-agents",
                    json={"slots": ["agent", "agent"]}, headers=OWNER)
    assert r.status_code == 200
    assert r.json()["data"]["cleared"] == {"agent": 1}  # cleared once, not twice


@pytest.mark.asyncio
async def test_apply_to_agents_writes_audit_snapshot(client, db):
    await _seed(db, "a1", "owner1", slot="agent", model="pinned-x")
    r = client.post("/api/providers/slots/apply-to-agents",
                    json={"slots": ["agent"]}, headers=OWNER)
    assert r.status_code == 200
    audit = await db.get("agent_slot_clear_audit", {"agent_id": "a1"})
    assert len(audit) == 1 and audit[0]["model"] == "pinned-x"


@pytest.mark.asyncio
async def test_apply_to_agents_bad_slot_is_fail_closed(client, db):
    # A valid slot alongside a bad one must NOT be partially cleared: the whole
    # request is rejected up front, before any delete.
    await _seed(db, "a1", "owner1", slot="agent")
    r = client.post("/api/providers/slots/apply-to-agents",
                    json={"slots": ["agent", "bogus"]}, headers=OWNER)
    assert r.status_code == 400
    assert await db.get_one("agent_slots", {"agent_id": "a1", "slot_name": "agent"}) is not None


def test_override_stats_requires_identity(client):
    r = client.get("/api/providers/slots/override-stats")  # no X-User-Id
    assert r.status_code == 401
