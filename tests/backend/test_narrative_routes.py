"""
@file_name: test_narrative_routes.py
@author:
@date: 2026-08-10
@description: Route-level tests for the narrative MCP data-access-seam
endpoints (view_narrative / switch_narrative / create_narrative).

Covers: the canonical ownership gate (non-owner 403 shape, unknown-agent
404 shape — same pattern as test_channel_routes_owner_gate.py /
test_awareness_create_missing.py), the happy paths for all three
endpoints, and the underlying-failure (200 + success:false) shape.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from narranexus_plugins.basic_info_module import routes as nr


class _FakeNarrativeRow(dict):
    pass


class _FakeDb:
    """Minimal AsyncDatabaseClient stand-in: get_one / get / get_by_ids are
    used by this route file (no raw SQL)."""

    def __init__(self, narratives=None, links=None, chat_memory=None, events=None):
        self._narratives = narratives or {}
        self._links = links or {}
        self._chat_memory = chat_memory or {}
        self._events = events or {}
        self.fanned_out_ids = None  # last ids handed to get_by_ids
        self.links_order_by = "__unset__"  # order_by passed to the links query

    async def get_one(self, table, filters):
        if table == "narratives":
            return self._narratives.get(filters.get("narrative_id"))
        if table == "events":
            row = self._events.get(filters.get("event_id"))
            # view_event scopes by agent_id — a row for another agent reads as
            # not found (the raw-SQL tool it replaces had no such scope).
            if row and row.get("agent_id") != filters.get("agent_id"):
                return None
            return row
        if table == "instance_json_format_memory_chat":
            return self._chat_memory.get(filters.get("instance_id"))
        raise AssertionError(f"unexpected get_one table: {table}")

    async def get(self, table, filters, limit=None, offset=None, order_by=None):
        if table == "instance_narrative_links":
            self.links_order_by = order_by  # for the ordering assertion
            rows = list(self._links.get(filters.get("narrative_id"), []))
            # Honor order_by so the truncation-order fix is actually exercised:
            # created_at DESC = newest first (rows are seeded oldest-first).
            if order_by == "created_at DESC":
                rows = list(reversed(rows))
            return rows[:limit] if limit else rows
        raise AssertionError(f"unexpected get table: {table}")

    async def get_by_ids(self, table, id_field, ids):
        # Single-query batch fetch (the N+1 fix uses this instead of a
        # per-instance get_one loop). Records the fan-out for cap assertions.
        if table == "instance_json_format_memory_chat":
            self.fanned_out_ids = list(ids)
            # Real contract: order-preserving, MISSING ids padded with None.
            return [self._chat_memory.get(i) for i in ids]
        raise AssertionError(f"unexpected get_by_ids table: {table}")


class _FakeNarrative:
    def __init__(self, id_):
        self.id = id_


@pytest.fixture
def fake_db():
    return _FakeDb(
        narratives={
            "nar_mine": {
                "narrative_id": "nar_mine",
                "agent_id": "agent_mine",
                "narrative_info": {
                    "name": "Trip planning",
                    "description": "Planning a trip",
                    "current_summary": "Discussing destinations",
                },
                "topic_keywords": ["travel", "trip"],
            },
            "nar_other_agent": {
                "narrative_id": "nar_other_agent",
                "agent_id": "agent_theirs",
                "narrative_info": {"name": "Not yours"},
                "topic_keywords": [],
            },
            "nar_partial": {
                "narrative_id": "nar_partial",
                "agent_id": "agent_mine",
                "narrative_info": {"name": "Interrupted first turn"},
                "topic_keywords": [],
            },
        },
        links={
            "nar_mine": [{"instance_id": "chat_abc123"}, {"instance_id": "aware_ignored"}],
            # A chat_ instance linked (step_1) whose memory row never landed
            # (step_5) — get_by_ids pads it with None.
            "nar_partial": [{"instance_id": "chat_abc123"}, {"instance_id": "chat_no_mem"}],
        },
        chat_memory={
            "chat_abc123": {
                "memory": {
                    "messages": [
                        {
                            "role": "user",
                            "content": "Where should I go?",
                            "meta_data": {"timestamp": "2026-08-10T10:00:00", "event_id": "evt_1"},
                        },
                        {
                            "role": "assistant",
                            "content": "How about Kyoto?",
                            "meta_data": {"timestamp": "2026-08-10T10:00:05", "event_id": "evt_2"},
                        },
                    ]
                }
            }
        },
        events={
            "evt_mine": {
                "event_id": "evt_mine", "agent_id": "agent_mine", "narrative_id": "nar_mine",
                "trigger": "manual", "trigger_source": "user", "env_context": {"input": "go"},
                "final_output": "done", "event_log": "trace", "created_at": "2026-08-10T10:00:00",
            },
            "evt_theirs": {
                "event_id": "evt_theirs", "agent_id": "agent_theirs", "narrative_id": "nar_other_agent",
                "trigger": "manual", "trigger_source": "user", "env_context": {},
                "final_output": "", "event_log": "", "created_at": "2026-08-10T10:00:00",
            },
        },
    )


@pytest.fixture
def client(monkeypatch, fake_db):
    async def _db():
        return fake_db

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(nr, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(nr.router, prefix="/api/agents")
    c = TestClient(app)
    return c


OWNER_HEADERS = {"x-test-user": "u1"}


# ---------------------------------------------------------------------------
# Ownership gate — one representative chain per HTTP verb, per
# test_channel_routes_owner_gate.py's precedent (the other endpoints share
# the same assert_owned wiring).
# ---------------------------------------------------------------------------


def test_view_narrative_non_owner_is_denied(client):
    r = client.get("/api/agents/agent_theirs/narratives/nar_other_agent", headers=OWNER_HEADERS)
    assert r.status_code == 403


def test_view_narrative_unknown_agent_is_404(client):
    r = client.get("/api/agents/agent_ghost/narratives/nar_mine", headers=OWNER_HEADERS)
    assert r.status_code == 404


def test_switch_narrative_non_owner_is_denied(client):
    r = client.post("/api/agents/agent_theirs/narratives/nar_other_agent/switch", headers=OWNER_HEADERS)
    assert r.status_code == 403


def test_switch_narrative_unknown_agent_is_404(client):
    r = client.post("/api/agents/agent_ghost/narratives/nar_mine/switch", headers=OWNER_HEADERS)
    assert r.status_code == 404


def test_create_narrative_non_owner_is_denied(client):
    r = client.post(
        "/api/agents/agent_theirs/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "New topic"},
    )
    assert r.status_code == 403


def test_create_narrative_unknown_agent_is_404(client):
    r = client.post(
        "/api/agents/agent_ghost/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "New topic"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# view_narrative
# ---------------------------------------------------------------------------


def test_view_narrative_returns_full_thread(client):
    r = client.get("/api/agents/agent_mine/narratives/nar_mine", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["narrative_id"] == "nar_mine"
    assert body["name"] == "Trip planning"
    assert body["summary"] == "Discussing destinations"
    assert body["keywords"] == ["travel", "trip"]
    assert body["message_count"] == 2
    # only the chat_ instance's messages are pulled in, sorted by time
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"] == "Where should I go?"
    assert body["messages"][0]["event_id"] == "evt_1"


def test_view_narrative_not_found(client):
    r = client.get("/api/agents/agent_mine/narratives/nar_missing", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["error"]


def test_view_narrative_belonging_to_a_different_agent_reports_not_found(client):
    # agent_mine is a real, owned agent, but nar_other_agent belongs to
    # agent_theirs — cross-agent narrative access must not leak.
    r = client.get("/api/agents/agent_mine/narratives/nar_other_agent", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["error"]


def test_view_narrative_db_error_reports_success_false(client, monkeypatch):
    async def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(nr, "get_db_client", _boom)
    r = client.get("/api/agents/agent_mine/narratives/nar_mine", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "db unavailable" in body["error"]


# ---------------------------------------------------------------------------
# switch_narrative
# ---------------------------------------------------------------------------


def test_switch_narrative_success(client):
    r = client.post("/api/agents/agent_mine/narratives/nar_mine/switch", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["narrative_id"] == "nar_mine"


def test_switch_narrative_not_found(client):
    r = client.post("/api/agents/agent_mine/narratives/nar_missing/switch", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["error"]


# ---------------------------------------------------------------------------
# create_narrative
# ---------------------------------------------------------------------------


def test_create_narrative_success(client, monkeypatch):
    captured = {}

    async def fake_create(self, agent_id, user_id, title, description=""):
        captured["args"] = (agent_id, user_id, title, description)
        return _FakeNarrative("nar_new123")

    monkeypatch.setattr(nr.NarrativeService, "create_narrative", fake_create)

    r = client.post(
        "/api/agents/agent_mine/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "New topic", "description": "About something new"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["narrative_id"] == "nar_new123"
    assert body["title"] == "New topic"
    assert captured["args"] == ("agent_mine", "u1", "New topic", "About something new")


def test_create_narrative_requires_title(client):
    r = client.post(
        "/api/agents/agent_mine/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "   "},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "title" in body["error"]


def test_create_narrative_underlying_failure_is_success_false(client, monkeypatch):
    async def fake_create(self, agent_id, user_id, title, description=""):
        raise RuntimeError("boom")

    monkeypatch.setattr(nr.NarrativeService, "create_narrative", fake_create)

    r = client.post(
        "/api/agents/agent_mine/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "New topic"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "boom" in body["error"]


def _client_for(monkeypatch, db):
    """A TestClient wired to an arbitrary _FakeDb (owner = u1 for agent_mine)."""
    async def _db():
        return db

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(nr, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(nr.router, prefix="/api/agents")
    return TestClient(app)


def _nar_row(nid="nar_mine", agent="agent_mine"):
    return {
        "narrative_id": nid,
        "agent_id": agent,
        "narrative_info": {"name": "N", "description": "", "current_summary": ""},
        "topic_keywords": [],
    }


def test_chat_history_survives_when_newest_links_are_non_chat(monkeypatch):
    """Round-3 review #2: the pre-fix code fetched the first 200 links then
    filtered to chat_, so a narrative whose newest 200 links are non-chat
    (aware_/job_/…) lost ALL its chat history. The fix fetches 500 ordered by
    created_at DESC before filtering — the chat link must still come through."""
    # 599 non-chat links (seeded oldest→newest) then one chat link (newest).
    # 600 > _MAX_NARRATIVE_LINKS(500): without order_by the query would take
    # the OLDEST 500 (all aware_) and lose the chat entirely — so this pins
    # BOTH the raised cap+filter-after AND the created_at DESC ordering.
    links = [{"instance_id": f"aware_{i}"} for i in range(599)]
    links.append({"instance_id": "chat_real"})
    db = _FakeDb(
        narratives={"nar_mine": _nar_row()},
        links={"nar_mine": links},
        chat_memory={
            "chat_real": {
                "instance_id": "chat_real",
                "memory": {"messages": [
                    {"role": "user", "content": "hello",
                     "meta_data": {"timestamp": "2026-08-10T00:00:00", "event_id": "e1"}}
                ]},
            }
        },
    )
    client = _client_for(monkeypatch, db)
    r = client.get("/api/agents/agent_mine/narratives/nar_mine", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["message_count"] == 1  # the chat history was NOT dropped
    assert body["messages"][0]["content"] == "hello"
    assert body["truncated"] is False
    assert db.links_order_by == "created_at DESC"  # ordering is load-bearing here


def test_over_cap_chat_instances_flag_truncated_and_fan_out_100(monkeypatch):
    """>100 chat instances → truncated True and exactly 100 fanned out (not
    all of them)."""
    links = [{"instance_id": f"chat_{i}"} for i in range(130)]
    chat_memory = {
        f"chat_{i}": {"instance_id": f"chat_{i}", "memory": {"messages": []}}
        for i in range(130)
    }
    db = _FakeDb(
        narratives={"nar_mine": _nar_row()},
        links={"nar_mine": links},
        chat_memory=chat_memory,
    )
    client = _client_for(monkeypatch, db)
    r = client.get("/api/agents/agent_mine/narratives/nar_mine", headers=OWNER_HEADERS)
    assert r.status_code == 200
    assert r.json()["truncated"] is True
    assert len(db.fanned_out_ids) == 100  # capped, not all 130
    # The NEWEST 100 (chat_129..chat_30), not an arbitrary 100 — pins the
    # created_at DESC ordering, not just the cap.
    assert db.fanned_out_ids[0] == "chat_129"
    assert "chat_0" not in db.fanned_out_ids


# ---------------------------------------------------------------------------
# view_event (new seam-twin GET /{agent_id}/events/{event_id})
# ---------------------------------------------------------------------------


def test_view_event_non_owner_is_denied(client):
    r = client.get("/api/agents/agent_theirs/events/evt_theirs", headers=OWNER_HEADERS)
    assert r.status_code == 403


def test_view_event_returns_detail(client):
    r = client.get("/api/agents/agent_mine/events/evt_mine", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["event_id"] == "evt_mine"
    assert body["narrative_id"] == "nar_mine"
    assert body["trigger"] == "manual"
    assert body["input"] == "go"
    assert body["final_output"] == "done"
    assert body["event_log"] == "trace"


def test_view_event_not_found(client):
    r = client.get("/api/agents/agent_mine/events/evt_missing", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["error"]


def test_view_event_belonging_to_a_different_agent_reports_not_found(client):
    # evt_theirs is owned by agent_theirs; agent_mine (owned by the caller u1)
    # must not be able to read it — the seam scopes by agent_id.
    r = client.get("/api/agents/agent_mine/events/evt_theirs", headers=OWNER_HEADERS)
    assert r.status_code == 200
    assert r.json()["success"] is False


def test_view_narrative_with_a_memoryless_chat_instance_does_not_crash(client):
    # nar_partial links chat_abc123 (has memory) + chat_no_mem (no memory row).
    # get_by_ids pads chat_no_mem with None; the read must skip it, not 500 /
    # success:false. Regression guard for the get_by_ids None-contract bug.
    r = client.get("/api/agents/agent_mine/narratives/nar_partial", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["message_count"] == 2  # chat_abc123's two messages; chat_no_mem skipped
