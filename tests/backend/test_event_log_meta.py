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

from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.schema_registry import auto_migrate

import backend.routes.agents.chat_history as hist_mod


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
    import xyz_agent_context.utils.db.db_factory as db_factory_mod
    original_factory = db_factory_mod.get_db_client
    original_mod = hist_mod.get_db_client
    yield
    db_factory_mod.get_db_client = original_factory
    hist_mod.get_db_client = original_mod


def _build_client(db_client):
    app = FastAPI()
    app.include_router(hist_mod.router, prefix="/api/agents")

    async def _get_db_override():
        return db_client

    import xyz_agent_context.utils.db.db_factory as db_factory_mod

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


@pytest.mark.asyncio
async def test_meta_carries_input_lifecycle_and_costs(db_client):
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

    client = _build_client(db_client)
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
async def test_meta_graceful_on_legacy_row_without_lifecycle_or_costs(db_client):
    await _seed_event(
        db_client, event_id="evt_old",
        started_at=None, finished_at=None, env_context=None,
    )

    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_old").json()

    assert body["success"] is True
    meta = body["meta"]
    assert meta["input_text"] is None
    assert meta["duration_seconds"] is None
    assert meta["models"] == []
    assert meta["total_cost_usd"] is None


@pytest.mark.asyncio
async def test_meta_input_text_is_capped(db_client):
    await _seed_event(
        db_client, event_id="evt_big",
        env_context=json.dumps({"input": "x" * 20000}),
    )

    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_big").json()

    assert len(body["meta"]["input_text"]) <= 4000
