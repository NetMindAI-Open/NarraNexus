"""
@file_name: test_agent_create_stores_normalized.py
@author: NarraNexus
@date: 2026-08-17
@description: The stored form of an agent's name/description is a property of
the table, not of whichever writer happened to create the row.

`PUT /api/auth/agents` normalizes on the way in and compares normalized values
to decide whether a write is needed. If a CREATE path could store an
unnormalized name, that row would be permanently stuck: saving the same name
without the stray whitespace is judged a no-op and never written. So creation
has to store the same form updates do.

`AgentRepository.add_agent` enforces it for the paths that go THROUGH the
repository — the auth route, the social-network route and the MCP tool arrive
via `provision_new_agent`; arena provisioning and the migration applier call it
directly. It is not the only writer of the table: the paths that raw-insert
normalize at their own edge, and the enumeration (with the command that
re-derives it) lives in test_agents_row_writers_normalize.py. Keeping the list
in one place is deliberate — two copies is how it drifted the first time.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from narranexus.platform.repository import AgentRepository
from narranexus.platform.repository.user_repository import UserRepository

OWNER = "alice"


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
def seeded_user(db_client):
    asyncio.get_event_loop().run_until_complete(
        UserRepository(db_client).add_user(user_id=OWNER, user_type="individual")
    )


def _stored(db_client, agent_id):
    async def _read():
        return await AgentRepository(db_client).get_agent(agent_id)

    return asyncio.get_event_loop().run_until_complete(_read())


class TestRepositoryEdge:
    """The backstop every creation path shares."""

    def test_add_agent_stores_the_normalized_form(self, db_client):
        async def _go():
            repo = AgentRepository(db_client)
            await repo.add_agent(
                agent_id="agent_ws",
                agent_name="  小绿  ",
                created_by=OWNER,
                agent_description="  精通各地美食推荐  ",
            )
            return await repo.get_agent("agent_ws")

        agent = asyncio.get_event_loop().run_until_complete(_go())
        assert agent.agent_name == "小绿"
        assert agent.agent_description == "精通各地美食推荐"

    def test_a_row_created_with_whitespace_is_still_renameable(self, db_client):
        """The reason this matters at all.

        If creation stored ' 小绿 ', then renaming to '小绿' compares equal,
        issues no write, and the row can never be cleaned up.
        """
        async def _go():
            repo = AgentRepository(db_client)
            await repo.add_agent(
                agent_id="agent_ws2", agent_name=" 小绿 ", created_by=OWNER
            )
            await repo.update_agent("agent_ws2", {"agent_name": "小蓝"})
            return await repo.get_agent("agent_ws2")

        assert asyncio.get_event_loop().run_until_complete(_go()).agent_name == "小蓝"

    def test_update_agent_normalizes_too(self, db_client):
        async def _go():
            repo = AgentRepository(db_client)
            await repo.add_agent(
                agent_id="agent_ws3", agent_name="小绿", created_by=OWNER
            )
            await repo.update_agent("agent_ws3", {"agent_name": "  小蓝  "})
            return await repo.get_agent("agent_ws3")

        assert asyncio.get_event_loop().run_until_complete(_go()).agent_name == "小蓝"


class TestCreateRoute:
    def test_a_whitespace_only_name_falls_back_to_the_default(
        self, client, db_client, seeded_user
    ):
        """'   ' is truthy, so an `or` on the raw value skips the default and
        stores whitespace — a blank sidebar row, less identifiable than the
        agent_id fallback an empty name would produce."""
        res = client.post(
            "/api/auth/agents",
            json={"created_by": OWNER, "agent_name": "   "},
            headers={"X-User-Id": OWNER},
        )
        body = res.json()
        assert body["success"] is True, body.get("error")
        assert body["agent"]["name"] == "New Agent"
        assert _stored(db_client, body["agent"]["agent_id"]).agent_name == "New Agent"

    def test_surrounding_whitespace_is_stripped_on_create(
        self, client, db_client, seeded_user
    ):
        res = client.post(
            "/api/auth/agents",
            json={
                "created_by": OWNER,
                "agent_name": "  小绿  ",
                "agent_description": "  精通各地美食推荐  ",
            },
            headers={"X-User-Id": OWNER},
        )
        body = res.json()
        assert body["success"] is True, body.get("error")
        agent = _stored(db_client, body["agent"]["agent_id"])
        assert agent.agent_name == "小绿"
        assert agent.agent_description == "精通各地美食推荐"
