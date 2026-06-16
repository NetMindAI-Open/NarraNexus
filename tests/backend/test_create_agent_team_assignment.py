"""
@file_name: test_create_agent_team_assignment.py
@author: Bin Liang
@date: 2026-06-16
@description: #43 — clicking "Add agent" under a team attaches the new agent
to that team on creation. Ownership-checked: a foreign / missing team_id never
blocks creation, it just leaves the agent ungrouped.

Same mini-middleware fixture pattern as test_auth_identity_hardening.py:
X-User-Id -> request.state.user_id (what auth_middleware does in prod).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from xyz_agent_context.repository.user_repository import UserRepository
from xyz_agent_context.repository import TeamRepository, TeamMemberRepository


async def _async_return(value):
    return value


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


@pytest.fixture
def seeded(db_client):
    """Two users, each owning a team. Returns (alice_team_id, bob_team_id)."""
    async def _seed():
        users = UserRepository(db_client)
        await users.add_user(user_id="alice", user_type="individual")
        await users.add_user(user_id="bob", user_type="individual")
        teams = TeamRepository(db_client)
        t_alice = await teams.create_team(owner_user_id="alice", name="Alice Team")
        t_bob = await teams.create_team(owner_user_id="bob", name="Bob Team")
        return t_alice.team_id, t_bob.team_id

    return asyncio.get_event_loop().run_until_complete(_seed())


def _members(db_client, team_id):
    return asyncio.get_event_loop().run_until_complete(
        TeamMemberRepository(db_client).list_members_by_team(team_id)
    )


def test_create_agent_attaches_to_own_team(client, seeded, db_client):
    alice_team, _ = seeded
    resp = client.post(
        "/api/auth/agents",
        headers={"X-User-Id": "alice"},
        json={"agent_name": "A1", "team_id": alice_team},
    )
    assert resp.status_code == 200
    agent_id = resp.json()["agent"]["agent_id"]
    assert agent_id in _members(db_client, alice_team)


def test_create_agent_ignores_foreign_team(client, seeded, db_client):
    _, bob_team = seeded
    resp = client.post(
        "/api/auth/agents",
        headers={"X-User-Id": "alice"},
        json={"agent_name": "A2", "team_id": bob_team},
    )
    # creation still succeeds — alice just cannot drop her agent into bob's team
    assert resp.status_code == 200
    agent_id = resp.json()["agent"]["agent_id"]
    assert agent_id not in _members(db_client, bob_team)


def test_create_agent_without_team_is_ungrouped(client, seeded, db_client):
    alice_team, _ = seeded
    resp = client.post(
        "/api/auth/agents",
        headers={"X-User-Id": "alice"},
        json={"agent_name": "A3"},
    )
    assert resp.status_code == 200
    agent_id = resp.json()["agent"]["agent_id"]
    assert agent_id not in _members(db_client, alice_team)
