"""
@file_name: test_agents_recent_sort.py
@author: NarraNexus
@date: 2026-07-17
@description: Locks the ordering contract of GET /api/auth/agents — the flat
agent list is sorted by most-recent conversation, newest on top, with a
deterministic agent_id-ascending tie-break.

Why this exists: the route sorts with TWO stable passes (agent_id asc, then
activity desc via `list.sort(reverse=True)`), which relies on the non-obvious
"stable sort preserves prior order among equal keys" property. A well-meaning
"simplification" to a single pass —
    agents.sort(key=lambda a: (activity, a.agent_id), reverse=True)
— would REVERSE the agent_id tie-break too (desc instead of asc), silently
diverging from the frontend's ascending tie-break in
agentGroupUtils.sortAgentsByActivity. The tie test below fails the day that
happens.

Same fixture pattern as test_auth_identity_hardening.py: a mini middleware
maps X-User-Id -> request.state.user_id (what auth_middleware does in prod).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


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


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _seed_agent(db_client, agent_id: str, *, created_at: str = "2026-01-01T00:00:00Z"):
    return db_client.insert(
        "agents",
        {
            "agent_id": agent_id,
            "agent_name": agent_id,
            "created_by": "alice",
            "agent_create_time": created_at,
        },
    )


def _seed_reply(db_client, agent_id: str, created_at: str, *, idx: int = 0):
    """A persisted assistant reply — the source of `last_assistant_at`."""
    return db_client.insert(
        "events",
        {
            "event_id": f"evt_{agent_id}_{idx}",
            "trigger": "user_message",  # NOT message_bus, so it counts as a 1:1 reply
            "trigger_source": "test",
            "agent_id": agent_id,
            "user_id": "alice",
            "final_output": "a reply",
            "state": "completed",
            "created_at": created_at,
        },
    )


def _order(client, user_id: str = "alice") -> list[str]:
    resp = client.get("/api/auth/agents", headers={"X-User-Id": user_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True, body
    return [a["agent_id"] for a in body["agents"]]


def test_agents_sorted_by_most_recent_reply_first(client, db_client):
    # older reply, newer reply — newest conversation must come first.
    _run(_seed_agent(db_client, "agt_older"))
    _run(_seed_agent(db_client, "agt_newer"))
    _run(_seed_reply(db_client, "agt_older", "2026-01-02T00:00:00Z"))
    _run(_seed_reply(db_client, "agt_newer", "2026-06-01T00:00:00Z"))

    assert _order(client) == ["agt_newer", "agt_older"]


def test_only_the_latest_reply_per_agent_drives_order(client, db_client):
    # agt_b's most-recent reply is newer than agt_a's, even though agt_a also
    # has an older reply — the window/MAX must pick the newest per agent.
    _run(_seed_agent(db_client, "agt_a"))
    _run(_seed_agent(db_client, "agt_b"))
    _run(_seed_reply(db_client, "agt_a", "2026-03-01T00:00:00Z", idx=0))
    _run(_seed_reply(db_client, "agt_a", "2026-01-01T00:00:00Z", idx=1))
    _run(_seed_reply(db_client, "agt_b", "2026-04-01T00:00:00Z", idx=0))

    assert _order(client) == ["agt_b", "agt_a"]


def test_equal_activity_breaks_ties_by_agent_id_ascending(client, db_client):
    """THE contract this file exists for. Equal last_assistant_at → the
    smaller agent_id must come first (ascending), matching the frontend.
    A single-pass `sort(key=(activity, id), reverse=True)` would put the
    LARGER id first and fail here."""
    same = "2026-05-01T00:00:00Z"
    # Insert in a deliberately non-sorted order so a no-op / unstable sort
    # can't pass by accident.
    for aid in ("agt_m", "agt_a", "agt_z", "agt_c"):
        _run(_seed_agent(db_client, aid))
        _run(_seed_reply(db_client, aid, same))

    assert _order(client) == ["agt_a", "agt_c", "agt_m", "agt_z"]


def test_agent_without_replies_falls_back_to_created_at(client, db_client):
    # No events at all for either agent → order by created_at desc.
    _run(_seed_agent(db_client, "agt_new", created_at="2026-07-16T00:00:00Z"))
    _run(_seed_agent(db_client, "agt_old", created_at="2026-01-01T00:00:00Z"))

    assert _order(client) == ["agt_new", "agt_old"]


def test_a_reply_outranks_a_newer_creation_time(client, db_client):
    # agt_fresh was just created but never chatted; agt_chatted was created
    # long ago but has a reply newer than agt_fresh's creation → chatted wins.
    _run(_seed_agent(db_client, "agt_fresh", created_at="2026-07-01T00:00:00Z"))
    _run(_seed_agent(db_client, "agt_chatted", created_at="2026-01-01T00:00:00Z"))
    _run(_seed_reply(db_client, "agt_chatted", "2026-08-01T00:00:00Z"))

    assert _order(client) == ["agt_chatted", "agt_fresh"]
