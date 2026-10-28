"""
@file_name: test_auth_agents_directory.py
@author:
@date: 2026-09-04
@description: GET /api/auth/agents directory enrichment — the per-agent
    framework / model projection and the bound_channels column. Runs against
    the real in-memory SQLite (migrated) so the hand-written channel UNION is
    exercised as SQL, not mocked away. Both enrichments degrade to "unknown"
    rather than failing the route; that degradation is pinned here on purpose
    so it can never become silent by accident (PR #383 review I3).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.auth as auth_mod

OWNER = {"X-User-Id": "owner1"}


@pytest.fixture
def client(monkeypatch, db_client, tmp_path):
    from narranexus.platform.settings import settings
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    async def _get_db():
        return db_client
    # auth.py binds get_db_client at import time, so patch the module attribute.
    monkeypatch.setattr(auth_mod, "get_db_client", _get_db)
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app, raise_server_exceptions=False)


async def _agent(db, agent_id, owner, public=False):
    await db.insert("agents", {
        "agent_id": agent_id, "agent_name": agent_id, "created_by": owner,
        "is_public": 1 if public else 0,
    })


def _by_id(payload):
    return {a["agent_id"]: a for a in payload["agents"]}


@pytest.mark.asyncio
async def test_framework_and_model_only_for_own_agents(client, db_client):
    await _agent(db_client, "mine", "owner1")
    await _agent(db_client, "theirs", "owner2", public=True)
    await db_client.insert("user_slots", {
        "user_id": "owner1", "slot_name": "agent", "provider_id": "p1",
        "model": "my-model", "params_json": "{}", "agent_framework": "codex_cli",
    })
    await db_client.insert("user_slots", {
        "user_id": "owner2", "slot_name": "agent", "provider_id": "p2",
        "model": "their-secret-model", "params_json": "{}", "agent_framework": "claude_code",
    })
    r = client.get("/api/auth/agents", headers=OWNER)
    assert r.status_code == 200
    agents = _by_id(r.json())
    assert agents["mine"]["agent_framework"] == "codex_cli"
    assert agents["mine"]["model"] == "my-model"
    # someone else's public agent: listed, but its configuration is not ours to see
    assert agents["theirs"]["agent_framework"] is None
    assert agents["theirs"]["model"] is None
    assert agents["theirs"]["bound_channels"] == []


@pytest.mark.asyncio
async def test_framework_defaults_to_platform_default_without_any_slot_row(client, db_client):
    await _agent(db_client, "fresh", "owner1")
    r = client.get("/api/auth/agents", headers=OWNER)
    a = _by_id(r.json())["fresh"]
    assert a["agent_framework"] == "nexus_power"   # never a literal claude_code
    assert a["model"] is None


@pytest.mark.asyncio
async def test_bound_channels_carry_their_switch_state(client, db_client):
    await _agent(db_client, "a1", "owner1")
    # lark: switched OFF; slack: ON
    # batch 4: every IM channel's binding is one row of the generic channel_credentials table
    await db_client.insert("channel_credentials", {"channel": "lark", "agent_id": "a1", "enabled": 0, "external_id": "cli_1", "version": 0})
    await db_client.insert("channel_credentials", {"channel": "slack", "agent_id": "a1", "enabled": 1, "external_id": "bot_1", "version": 0})
    r = client.get("/api/auth/agents", headers=OWNER)
    bound = _by_id(r.json())["a1"]["bound_channels"]
    assert {b["channel"]: b["active"] for b in bound} == {"lark": False, "slack": True}
    # registry order: lark before slack
    assert [b["channel"] for b in bound] == ["lark", "slack"]

    # flipping the switch back on is reflected on the next read
    await db_client.update("channel_credentials", {"agent_id": "a1", "channel": "lark"}, {"enabled": 1})
    r = client.get("/api/auth/agents", headers=OWNER)
    bound = {b["channel"]: b["active"] for b in _by_id(r.json())["a1"]["bound_channels"]}
    assert bound["lark"] is True


@pytest.mark.asyncio
async def test_enrichment_failure_degrades_instead_of_failing_the_route(client, db_client, monkeypatch):
    await _agent(db_client, "a1", "owner1")

    # A registry entry naming a table that does not exist breaks the UNION.
    from narranexus.platform.channel.binding_tables import BindingSource

    monkeypatch.setattr(
        auth_mod, "channel_binding_sources",
        lambda: [BindingSource("ghost", "no_such_table", (), None)],
    )

    class _Boom:
        def __init__(self, db):
            pass

        async def owner_agents_overview(self, *a, **k):
            raise RuntimeError("slot service down")
    monkeypatch.setattr(auth_mod, "AgentSlotService", _Boom)

    r = client.get("/api/auth/agents", headers=OWNER)
    assert r.status_code == 200                      # the directory still answers
    a = _by_id(r.json())["a1"]
    assert a["agent_framework"] is None and a["model"] is None
    assert a["bound_channels"] == []
    assert a["name"] == "a1"                         # everything else intact
