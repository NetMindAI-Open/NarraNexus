"""
@file_name: test_event_log_meta.py
@author: Bin Liang
@date: 2026-07-23
@description: Tests for the run-level `meta` block of
GET /api/agents/{agent_id}/event-log/{event_id}.

The activity ("inner thought") card needs more than the loop timeline:
what input the agent received and from where, what it produced, when the
run started, how long it took, what it cost and on which models
(bug tracker: "Agent 内心活动显示优化"). All of that already exists in the
events row + cost_records — this endpoint now surfaces it as `meta`.

Covers:
- trigger/trigger_source/input_text (env_context.input) round-trip
- started_at/finished_at/duration_seconds/state
- cost aggregation across cost_records rows (models, cost, tokens)
- graceful nulls: legacy row without lifecycle timestamps / costs
- input_text is capped so a huge bus payload can't bloat the response
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.schema_registry import auto_migrate

import narranexus_plugins.chat_module.routes as hist_mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


@pytest.fixture(autouse=True)
def _restore_get_db():
    import narranexus.platform.utils.db.db_factory as db_factory_mod
    original_factory = db_factory_mod.get_db_client
    original_mod = hist_mod.get_db_client
    yield
    db_factory_mod.get_db_client = original_factory
    hist_mod.get_db_client = original_mod


def _build_client(db_client, monkeypatch, user_id="user_x"):
    app = FastAPI()

    # The route is owner-gated (2026-10-28): inject an identity and wire the
    # SDK ownership seam at the shared helper's db lookup.
    @app.middleware("http")
    async def _identity(request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or user_id
        return await call_next(request)

    from backend.plugin_sdk_host import install_web_host
    install_web_host()
    import backend.routes._ownership as own_mod

    async def _own_db():
        return db_client
    monkeypatch.setattr(own_mod, "get_db_client", _own_db)
    app.include_router(hist_mod.router, prefix="/api/agents")

    async def _get_db_override():
        return db_client

    import narranexus.platform.utils.db.db_factory as db_factory_mod

    db_factory_mod.get_db_client = _get_db_override
    hist_mod.get_db_client = _get_db_override
    return TestClient(app)


async def _seed_event(db, *, event_id="evt_meta1", agent_id="agent_a", **overrides):
    row = {
        "event_id": event_id,
        "trigger": "job",
        "trigger_source": "job",
        "agent_id": agent_id,
        "user_id": "user_x",
        "env_context": json.dumps({"input": "Run the daily briefing for markets"}),
        "event_log": json.dumps([
            {"content": {"type": "thinking", "content": "planning"}},
            {"content": {"type": "tool_call", "tool_name": "web_search", "arguments": {"q": "spx"}}},
            {"content": {"type": "tool_output", "output": "ok"}},
        ]),
        "final_output": "Briefing sent to the user.",
        "state": "completed",
        "started_at": "2026-07-23 08:00:00",
        "finished_at": "2026-07-23 08:01:30",
    }
    row.update(overrides)
    await db.insert("events", row)
    if not await db.get_one("agents", {"agent_id": agent_id}):
        await db.insert("agents", {"agent_id": agent_id, "agent_name": agent_id, "created_by": "user_x"})


@pytest.mark.asyncio
async def test_meta_carries_input_lifecycle_and_costs(db_client, monkeypatch):
    await _seed_event(db_client)
    for model, itok, otok, cost in (
        ("deepseek-v4", 1200, 300, 0.004),
        ("bge-m3", 50, 0, 0.0001),
    ):
        await db_client.insert("cost_records", {
            "agent_id": "agent_a", "event_id": "evt_meta1",
            "call_type": "agent_loop", "model": model,
            "input_tokens": itok, "output_tokens": otok,
            "total_cost_usd": cost,
        })

    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/event-log/evt_meta1").json()

    assert body["success"] is True
    meta = body["meta"]
    assert meta["trigger_source"] == "job"
    assert meta["input_text"] == "Run the daily briefing for markets"
    assert meta["final_output"] == "Briefing sent to the user."
    assert meta["state"] == "completed"
    assert meta["duration_seconds"] == 90.0
    assert sorted(meta["models"]) == ["bge-m3", "deepseek-v4"]
    assert meta["input_tokens"] == 1250
    assert meta["output_tokens"] == 300
    assert abs(meta["total_cost_usd"] - 0.0041) < 1e-9
    assert meta["tool_call_count"] == 1


@pytest.mark.asyncio
async def test_meta_includes_cache_buckets(db_client, monkeypatch):
    """input_tokens is only the full-rate bucket; the cache columns carry the
    bulk on a cache-warm run and must be surfaced separately, not dropped."""
    await _seed_event(db_client, event_id="evt_cache")
    await db_client.insert("cost_records", {
        "agent_id": "agent_a", "event_id": "evt_cache",
        "call_type": "agent_loop", "model": "claude-code",
        "input_tokens": 33, "output_tokens": 19_528,
        "cache_read_input_tokens": 735_147,
        "cache_creation_input_tokens": 134_071,
        "total_cost_usd": 2.198,
    })

    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/event-log/evt_cache").json()

    meta = body["meta"]
    assert meta["input_tokens"] == 33
    assert meta["cache_read_tokens"] == 735_147
    assert meta["cache_creation_tokens"] == 134_071


@pytest.mark.asyncio
async def test_meta_graceful_on_legacy_row_without_lifecycle_or_costs(db_client, monkeypatch):
    await _seed_event(
        db_client, event_id="evt_old",
        started_at=None, finished_at=None, env_context=None,
    )

    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/event-log/evt_old").json()

    assert body["success"] is True
    meta = body["meta"]
    assert meta["input_text"] is None
    assert meta["duration_seconds"] is None
    assert meta["models"] == []
    assert meta["total_cost_usd"] is None


@pytest.mark.asyncio
async def test_meta_input_text_is_capped(db_client, monkeypatch):
    await _seed_event(
        db_client, event_id="evt_big",
        env_context=json.dumps({"input": "x" * 20000}),
    )

    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/event-log/evt_big").json()

    assert len(body["meta"]["input_text"]) <= 4000


@pytest.mark.asyncio
async def test_timeline_tool_output_inherits_call_name(db_client, monkeypatch):
    """Stored tool_output entries carry no tool_name; the timeline must give
    them the preceding call's name — never a literal placeholder. A reverted
    fix shows "[output] unknown" on every row of the disclosure."""
    await _seed_event(db_client, event_id="evt_names")
    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/event-log/evt_names").json()

    assert body["success"] is True
    timeline = body["timeline"]
    outputs = [e for e in timeline if e["type"] == "tool_output"]
    assert outputs and outputs[0]["tool_name"] == "web_search"
    assert "unknown" not in json.dumps(timeline)


@pytest.mark.asyncio
async def test_timeline_parallel_outputs_pair_by_call_id(db_client, monkeypatch):
    """Parallel tool calls: every call lands before any output and the
    outputs return in completion order. "Nearest preceding call" would
    confidently attach the WRONG name — pairing must go by tool_call_id,
    in both the timeline and the grouped tool_calls view."""
    await _seed_event(
        db_client,
        event_id="evt_parallel",
        event_log=json.dumps([
            {"content": {"type": "tool_call", "tool_call_id": "id1",
                         "tool_name": "read_file", "arguments": {"path": "a"}}},
            {"content": {"type": "tool_call", "tool_call_id": "id2",
                         "tool_name": "web_search", "arguments": {"q": "spx"}}},
            {"content": {"type": "tool_output", "tool_call_id": "id2",
                         "output": "search results"}},
            {"content": {"type": "tool_output", "tool_call_id": "id1",
                         "output": "file body"}},
        ]),
    )
    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/event-log/evt_parallel").json()
    assert body["success"] is True

    outputs = [e for e in body["timeline"] if e["type"] == "tool_output"]
    assert [(o["tool_name"], o["tool_output"]) for o in outputs] == [
        ("web_search", "search results"),
        ("read_file", "file body"),
    ]

    calls = {c["tool_name"]: c["tool_output"] for c in body["tool_calls"]}
    assert calls == {"read_file": "file body", "web_search": "search results"}


@pytest.mark.asyncio
async def test_timeline_empty_named_call_does_not_borrow_sibling_name(db_client, monkeypatch):
    """A persisted call whose name is KNOWN-empty (the writer refuses to
    invent placeholders) must keep its output unnamed — inheriting a
    parallel sibling's name would be a confidently wrong label."""
    await _seed_event(
        db_client,
        event_id="evt_emptyname",
        event_log=json.dumps([
            {"content": {"type": "tool_call", "tool_call_id": "id1",
                         "tool_name": "", "arguments": {}}},
            {"content": {"type": "tool_call", "tool_call_id": "id2",
                         "tool_name": "web_search", "arguments": {"q": "x"}}},
            {"content": {"type": "tool_output", "tool_call_id": "id1",
                         "output": "anon result"}},
        ]),
    )
    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/event-log/evt_emptyname").json()
    outputs = {e["tool_output"]: e["tool_name"] for e in body["timeline"] if e["type"] == "tool_output"}
    assert outputs["anon result"] == ""
    # BOTH views must agree — they read one shared index now, and a drift
    # would make the endpoint contradict itself.
    grouped = {c["tool_name"]: c["tool_output"] for c in body["tool_calls"]}
    assert grouped == {"": "anon result", "web_search": None}


@pytest.mark.asyncio
async def test_timeline_output_with_unseen_id_stays_unnamed(db_client, monkeypatch):
    """An output carrying an id we never saw a call for: we OUGHT to know
    the owner and genuinely don't — an honest blank, never a sibling's
    name."""
    await _seed_event(
        db_client,
        event_id="evt_ghostid",
        event_log=json.dumps([
            {"content": {"type": "tool_call", "tool_call_id": "id1",
                         "tool_name": "web_search", "arguments": {"q": "x"}}},
            {"content": {"type": "tool_output", "tool_call_id": "ghost",
                         "output": "orphan"}},
            {"content": {"type": "tool_output", "tool_call_id": "id1",
                         "output": "results"}},
        ]),
    )
    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/event-log/evt_ghostid").json()
    outputs = {e["tool_output"]: e["tool_name"] for e in body["timeline"] if e["type"] == "tool_output"}
    assert outputs == {"orphan": "", "results": "web_search"}


async def test_event_log_detail_is_owner_only(db_client, monkeypatch):
    """A stranger with both ids gets nothing — the log carries thinking and
    every tool call. Remove the ownership gate and this goes green for them."""
    client = _build_client(db_client, monkeypatch)
    await _seed_event(db_client, event_id="evt_priv")
    r = client.get("/api/agents/agent_a/event-log/evt_priv", headers={"X-User-Id": "stranger"})
    assert r.status_code in (403, 404), r.text
    assert "planning" not in r.text
    ok = client.get("/api/agents/agent_a/event-log/evt_priv")
    assert ok.status_code == 200 and ok.json()["success"] is True
