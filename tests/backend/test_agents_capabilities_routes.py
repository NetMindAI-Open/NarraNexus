"""
@file_name: test_agents_capabilities_routes.py
@author: Bin Liang
@date: 2026-09-04
@description: /api/agents/{agent_id}/capabilities — owner-gated (403/404), lists every module with its state and budget, PUT flips a module (base modules 400), DELETE resets to the default rule.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.agents.capabilities as mod


def _client(db_client, monkeypatch, viewer_id="u1"):
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/agents")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        request.state.role = "user"
        return await call_next(request)

    async def _db():
        return db_client

    monkeypatch.setattr(mod, "get_db_client", _db)
    # the route's ownership check is the shared backend.routes._ownership helper
    from backend.routes import _ownership

    monkeypatch.setattr(_ownership, "get_db_client", _db)
    return TestClient(app)


@pytest.mark.asyncio
async def test_capabilities_routes(db_client, monkeypatch):
    await db_client.insert("agents", {"agent_id": "ag1", "agent_name": "A", "created_by": "u1"})
    c = _client(db_client, monkeypatch)
    r = c.get("/api/agents/ag1/capabilities")
    assert r.status_code == 200
    data = r.json()["data"]
    names = [i["module_class"] for i in data["capabilities"]]
    assert "ChatModule" in names and "JobModule" in names and "budget" in data
    chat = next(i for i in data["capabilities"] if i["module_class"] == "ChatModule")
    assert chat["locked"] and chat["enabled"] and chat["builtin"]
    assert c.put("/api/agents/ag1/capabilities/ChatModule", json={"enabled": False}).status_code == 400
    r = c.put("/api/agents/ag1/capabilities/JobModule", json={"enabled": False})
    assert r.status_code == 200 and r.json()["data"] == {"module_class": "JobModule", "enabled": False}
    job = next(i for i in c.get("/api/agents/ag1/capabilities").json()["data"]["capabilities"] if i["module_class"] == "JobModule")
    assert job["enabled"] is False and job["explicit"] is True
    assert c.delete("/api/agents/ag1/capabilities/JobModule").json()["data"]["reset"] is True
    assert c.put("/api/agents/ag1/capabilities/NopeModule", json={"enabled": True}).status_code == 400
    # ownership
    assert c.get("/api/agents/ghost/capabilities").status_code == 404
    other = _client(db_client, monkeypatch, viewer_id="u2")
    assert other.get("/api/agents/ag1/capabilities").status_code == 403
    assert other.put("/api/agents/ag1/capabilities/JobModule", json={"enabled": True}).status_code == 403
    # DELETE goes through the same _require_owner; without this line, dropping
    # that call let a non-owner reset any agent's capability overrides.
    assert other.delete("/api/agents/ag1/capabilities/JobModule").status_code == 403
