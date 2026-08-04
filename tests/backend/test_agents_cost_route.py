"""
@file_name: test_agents_cost_route.py
@author: NarraNexus
@date: 2026-07-30
@description: Tests for GET /api/agents/{agent_id}/costs aggregation.

Why these tests exist: the cost popover read only input_tokens +
output_tokens, while both LLM paths record the prompt-cache buckets in
separate columns (cache_read_input_tokens / cache_creation_input_tokens).
On a cache-warm agent those columns hold >99% of the input-side tokens
(live case: agent_39b2b72b823b showed "input 213" for a run whose real
input side was 1.2M tokens), which made the helper look bigger than the
main loop. The endpoint must surface all three buckets so the frontend
can render honest totals.

Covers:
- per-model / total / daily aggregation includes both cache columns
- records echo the cache columns
- rows written without cache activity aggregate as 0
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.schema_registry import auto_migrate

import backend.routes.agents.cost as cost_mod


VIEWER = "user_x"


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


def _build_client(db_client, monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(cost_mod.router, prefix="/api/agents")

    async def _get_db_override():
        return db_client

    async def _viewer_override(request):
        return VIEWER

    monkeypatch.setattr(cost_mod, "get_db_client", _get_db_override)
    monkeypatch.setattr(cost_mod, "resolve_current_user_id", _viewer_override)
    return TestClient(app)


async def _seed_agent(db, agent_id="agent_a", created_by=VIEWER):
    await db.insert("agents", {
        "agent_id": agent_id,
        "agent_name": "Test agent",
        "created_by": created_by,
    })


async def _seed_cost(db, **overrides):
    row = {
        "agent_id": "agent_a",
        "event_id": None,
        "call_type": "agent_loop",
        "model": "claude-code",
        "input_tokens": 0,
        "output_tokens": 0,
        "total_cost_usd": 0.0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "created_at": "2026-07-30 08:00:00",
    }
    row.update(overrides)
    await db.insert("cost_records", row)


@pytest.mark.asyncio
async def test_aggregation_includes_cache_buckets(db_client, monkeypatch):
    """The live regression: uncached input is tiny, cache columns are the bulk."""
    await _seed_agent(db_client)
    # Main loop: 33 uncached / 735k cache read / 134k cache write.
    await _seed_cost(
        db_client,
        call_type="agent_loop",
        input_tokens=33,
        output_tokens=19_528,
        cache_read_input_tokens=735_147,
        cache_creation_input_tokens=134_071,
        total_cost_usd=2.198,
    )
    # Helper: 180 uncached / 281k cache read / 47k cache write.
    await _seed_cost(
        db_client,
        call_type="llm_function",
        model="haiku",
        input_tokens=180,
        output_tokens=20_721,
        cache_read_input_tokens=281_434,
        cache_creation_input_tokens=47_003,
        total_cost_usd=0.191,
        created_at="2026-07-30 09:00:00",
    )

    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/costs").json()

    assert body["success"] is True
    summary = body["summary"]

    assert summary["total_input_tokens"] == 213
    assert summary["total_output_tokens"] == 40_249
    assert summary["total_cache_read_tokens"] == 1_016_581
    assert summary["total_cache_creation_tokens"] == 181_074

    main = summary["by_model"]["__main_model__"]
    assert main["input_tokens"] == 33
    assert main["cache_read_tokens"] == 735_147
    assert main["cache_creation_tokens"] == 134_071
    assert main["call_count"] == 1

    helper = summary["by_model"]["__helper_model__"]
    assert helper["cache_read_tokens"] == 281_434
    assert helper["cache_creation_tokens"] == 47_003

    (daily,) = summary["daily"]
    assert daily["date"] == "2026-07-30"
    assert daily["cache_read_tokens"] == 1_016_581
    assert daily["cache_creation_tokens"] == 181_074

    # Records echo the buckets so the raw view stays inspectable.
    by_type = {r["call_type"]: r for r in body["records"]}
    assert by_type["agent_loop"]["cache_read_tokens"] == 735_147
    assert by_type["llm_function"]["cache_creation_tokens"] == 47_003


@pytest.mark.asyncio
async def test_rows_without_cache_activity_aggregate_as_zero(db_client, monkeypatch):
    """A row written before cache telemetry (columns defaulted to 0) stays 0."""
    await _seed_agent(db_client)
    await db_client.insert("cost_records", {
        "agent_id": "agent_a",
        "call_type": "agent_loop",
        "model": "claude-code",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_cost_usd": 0.0,
        "created_at": "2026-07-30 08:00:00",
    })

    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/agent_a/costs").json()

    assert body["success"] is True
    summary = body["summary"]
    assert summary["total_input_tokens"] == 100
    assert summary["total_cache_read_tokens"] == 0
    assert summary["total_cache_creation_tokens"] == 0
    assert body["records"][0]["cache_read_tokens"] == 0


@pytest.mark.asyncio
async def test_all_agents_view_includes_cache_buckets(db_client, monkeypatch):
    await _seed_agent(db_client, agent_id="agent_a")
    await _seed_agent(db_client, agent_id="agent_b")
    await _seed_cost(db_client, agent_id="agent_a", cache_read_input_tokens=1_000)
    await _seed_cost(db_client, agent_id="agent_b", cache_creation_input_tokens=500)

    client = _build_client(db_client, monkeypatch)
    body = client.get("/api/agents/_all/costs").json()

    assert body["success"] is True
    assert body["summary"]["total_cache_read_tokens"] == 1_000
    assert body["summary"]["total_cache_creation_tokens"] == 500
