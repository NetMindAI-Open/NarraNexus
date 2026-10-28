"""
@file_name: test_profile_seam_route.py
@author:
@date: 2026-08-10
@description: Route-level tests for the agent-profile MCP data-access-seam
endpoint (POST /{agent_id}/profile/update). Owner-gated byte-parity twin of the
update_agent_profile tool: it runs the SAME shared update_agent_profile_from_args
that the seam's DirectStore runs, so we drive it against a real in-memory sqlite
and assert it returns the tool's exact string in a {"message": ...} envelope.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import narranexus_plugins.awareness_module.routes_profile as pr

OWNER_ID = "user_tc"
OWNER = {"x-test-user": OWNER_ID}


@pytest.fixture
async def client(monkeypatch):
    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    backend_db = SQLiteBackend(":memory:")
    await backend_db.initialize()
    await auto_migrate(backend_db)
    db = await AsyncDatabaseClient.create_with_backend(backend_db)

    await db.insert("agents", {
        "agent_id": "agent_mine", "agent_name": "Pengu", "created_by": OWNER_ID,
        "agent_description": "", "is_public": 0,
    })
    await db.insert("module_instances", {
        "instance_id": "aware_mine", "agent_id": "agent_mine", "user_id": OWNER_ID,
        "module_class": "AwarenessModule", "status": "active",
    })
    await db.insert("instance_awareness", {
        "instance_id": "aware_mine",
        "awareness": "# Agent Awareness Profile\n\n## Role\n- I am Pengu\n",
    })

    async def _db():
        return db

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(pr, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": OWNER_ID, "agent_theirs": "user_other"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(pr.router, prefix="/api/agents")
    try:
        yield TestClient(app)
    finally:
        await db.close()


def test_profile_update_non_owner_is_denied(client):
    r = client.post("/api/agents/agent_theirs/profile/update", headers=OWNER, json={"new_name": "X"})
    assert r.status_code == 403


def test_profile_update_rename_returns_the_tool_string(client):
    r = client.post("/api/agents/agent_mine/profile/update", headers=OWNER, json={"new_name": "Quacker"})
    assert r.status_code == 200
    msg = r.json()["message"]
    assert msg.startswith("Profile updated successfully")
    assert "agent_name" in msg


def test_profile_update_nothing_passed_is_an_explicit_error(client):
    r = client.post("/api/agents/agent_mine/profile/update", headers=OWNER, json={})
    assert r.status_code == 200
    assert r.json()["message"].startswith("Error: nothing to update")


def test_profile_update_resaving_same_description_is_no_op_not_error(client):
    # MATCHED-vs-CHANGED parity guard (non-vacuous): update_agent returns
    # cursor.rowcount = matched on sqlite / changed on MySQL. Re-saving the
    # IDENTICAL description must return "No changes needed" — the equality
    # short-circuit is what makes this the same on both dialects instead of a
    # false "did not apply" on cloud. Stays green on sqlite; the short-circuit
    # (not rowcount) is what the assertion pins.
    first = client.post("/api/agents/agent_mine/profile/update", headers=OWNER,
                        json={"new_description": "a helpful reviewer"})
    assert first.json()["message"].startswith("Profile updated successfully")
    again = client.post("/api/agents/agent_mine/profile/update", headers=OWNER,
                        json={"new_description": "a helpful reviewer"})
    assert again.status_code == 200
    assert again.json()["message"].startswith("No changes needed")


def test_profile_update_over_length_name_is_rejected_not_written(client):
    # Bind to AGENT_TEXT_MAX_LENGTH (255): a >255 name must be rejected with a
    # readable string, never written. Non-vacuous — without the shared-fn length
    # check, sqlite (TEXT) would accept it and the assertion would see
    # "Profile updated successfully"; on MySQL the same write would 1406 / make
    # the row unreadable (NetMindAI-Open#71).
    r = client.post("/api/agents/agent_mine/profile/update", headers=OWNER,
                    json={"new_name": "x" * 256})
    assert r.status_code == 200
    assert r.json()["message"].startswith("Error: new_name is too long")
