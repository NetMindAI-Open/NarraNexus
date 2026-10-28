"""
@file_name: test_general_memory_routes.py
@author:
@date: 2026-08-10
@description: Route-level tests for backend/routes/agents/general_memory.py
(PR-2: the MCP data-access seam's HTTP counterpart of remember / grep_memory /
memory_retain).

Follows the same fixture shape as test_channel_routes_owner_gate.py — a real
TestClient chain proves the routes are actually wired to the canonical
``assert_owned`` gate, not just that the helper works in isolation. The
MemoryCoordinator/MemoryEngine seam is monkeypatched with stubs so these tests
never touch a real database; a separate concern (owner gate wiring vs
MemoryEngine correctness, the latter is covered by the memory package's own
tests).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import backend.routes.agents.general_memory as gm
from backend.routes.agents.general_memory import router as general_memory_router


class _StubRecord:
    def __init__(self, content_text, tags=None, source_ref=None, created_at=None):
        self.content_text = content_text
        self.tags = tags or []
        self.source_ref = source_ref
        self.created_at = created_at
        self.record_id = "mem_stub1"


class _StubHit:
    def __init__(self, kind, content_text, **record_kwargs):
        self.kind = kind
        self.record = _StubRecord(content_text, **record_kwargs)


class _StubEngine:
    """Stands in for MemoryEngine — records its construction args and, for
    ``retain``, echoes back the record with a deterministic id."""

    def __init__(self, db, agent_id, **kwargs):
        self.db = db
        self.agent_id = agent_id

    async def retain(self, record):
        record.record_id = "mem_new1"
        return record


class _StubCoordinator:
    """Stands in for MemoryCoordinator — deterministic hits, no db access."""

    def __init__(self, engine):
        self.engine = engine

    async def remember(self, query, *, limit=15, **kwargs):
        return [_StubHit("observation", f"fact about {query}")]

    async def grep_memory(self, pattern, *, regex=False, limit=30, **kwargs):
        return ([_StubHit("chat", f"line containing {pattern}")], False)


@pytest.fixture
def client(monkeypatch):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(gm, "get_db_client", _db)
    monkeypatch.setattr(gm, "MemoryEngine", _StubEngine)
    monkeypatch.setattr(gm, "MemoryCoordinator", _StubCoordinator)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(general_memory_router, prefix="/api/agents")
    return TestClient(app)


# ── ownership gate ───────────────────────────────────────────────────────


def test_remember_non_owner_is_denied(client):
    r = client.get(
        "/api/agents/agent_theirs/memory/remember",
        params={"query": "hello"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 403


def test_grep_non_owner_is_denied(client):
    r = client.get(
        "/api/agents/agent_theirs/memory/grep",
        params={"pattern": "abc"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 403


def test_retain_non_owner_is_denied(client):
    r = client.post(
        "/api/agents/agent_theirs/memory/retain",
        json={"content": "a fact"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 403


def test_remember_unknown_agent_reports_not_found(client):
    r = client.get(
        "/api/agents/agent_ghost/memory/remember",
        params={"query": "hello"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 404


# ── happy path ────────────────────────────────────────────────────────────


def test_remember_owner_returns_formatted_hits(client):
    r = client.get(
        "/api/agents/agent_mine/memory/remember",
        params={"query": "project deadline"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["query"] == "project deadline"
    assert body["memories"] == [
        {"kind": "observation", "memory": "fact about project deadline", "when": None, "tags": []}
    ]


def test_grep_owner_returns_formatted_matches(client):
    r = client.get(
        "/api/agents/agent_mine/memory/grep",
        params={"pattern": "order-123", "regex": "false", "limit": 5},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["pattern"] == "order-123"
    assert body["matches"] == [
        {"kind": "chat", "memory": "line containing order-123", "when": None, "tags": []}
    ]


def test_retain_owner_persists_and_returns_record_id(client):
    r = client.post(
        "/api/agents/agent_mine/memory/retain",
        json={"content": "the user prefers dark mode", "source": "MEMORY.md"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"success": True, "record_id": "mem_new1"}


def test_local_mode_without_identity_allows_the_call(client):
    """No x-test-user header -> request.state.user_id is None -> local mode,
    per _ownership.py's security posture: enforcement is skipped."""
    r = client.get(
        "/api/agents/agent_theirs/memory/remember",
        params={"query": "hello"},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True


# ── input validation / underlying failure ───────────────────────────────


def test_retain_rejects_empty_content(client):
    r = client.post(
        "/api/agents/agent_mine/memory/retain",
        json={"content": "   "},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "content is empty" in body["error"]


def test_remember_underlying_failure_returns_success_false(client, monkeypatch):
    async def _boom(self, query, *, limit=15, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(_StubCoordinator, "remember", _boom)

    r = client.get(
        "/api/agents/agent_mine/memory/remember",
        params={"query": "hello"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "db exploded" in body["error"]
    assert body["memories"] == []


def test_retain_underlying_failure_returns_success_false(client, monkeypatch):
    async def _boom(self, record):
        raise RuntimeError("write failed")

    monkeypatch.setattr(_StubEngine, "retain", _boom)

    r = client.post(
        "/api/agents/agent_mine/memory/retain",
        json={"content": "a fact"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "write failed" in body["error"]


def test_grep_regex_mode_is_now_served(client):
    """regex mode used to be REFUSED over HTTP (attacker-controlled pattern on
    the shared loop = self-service DoS). It is now served because the engine
    (retrieval.grep_filter) runs the untrusted pattern through the `regex`
    package with a per-match timeout + total budget (see test_grep_redos.py for
    the engine-level ReDoS proof). The route just passes regex through — no
    refusal branch."""
    r = client.get(
        "/api/agents/agent_mine/memory/grep",
        params={"pattern": "(a+)+$", "regex": "true"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "regex mode is not available" not in str(body)


def test_grep_pattern_and_limit_are_bounded(client):
    r = client.get(
        "/api/agents/agent_mine/memory/grep",
        params={"pattern": "x" * 300},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 422  # pattern max_length=256
    r = client.get(
        "/api/agents/agent_mine/memory/grep",
        params={"pattern": "x", "limit": 9999},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 422  # limit le=200
