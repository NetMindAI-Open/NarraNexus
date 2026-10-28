from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _FakeDb:
    def __init__(self):
        self.rows = []

    async def get_one(self, table, filters):
        return next(
            (
                row for row in self.rows
                if table == "product_analytics_events"
                and row["analytics_event_id"] == filters["analytics_event_id"]
            ),
            None,
        )

    async def insert(self, table, row):
        assert table == "product_analytics_events"
        if any(
            item["analytics_event_id"] == row["analytics_event_id"]
            for item in self.rows
        ):
            raise RuntimeError("UNIQUE constraint failed: analytics_event_id")
        self.rows.append(row)


@pytest.mark.asyncio
async def test_track_persists_indexed_first_party_dimensions(monkeypatch):
    import narranexus.platform.analytics as analytics

    db = _FakeDb()
    monkeypatch.setattr(analytics, "_opted_out", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "narranexus.platform.utils.get_db_client", AsyncMock(return_value=db)
    )

    await analytics.track(
        user_id="user-1",
        event="message_accepted",
        event_id="event-fixed-1",
        properties={
            "source": "backend",
            "run_id": "run-1",
            "trigger_source": "chat",
            "latency_ms": 123,
        },
    )
    await analytics.track(
        user_id="user-1",
        event="message_accepted",
        event_id="event-fixed-1",
    )

    assert len(db.rows) == 1
    assert db.rows[0]["user_id"] == "user-1"
    assert db.rows[0]["event_name"] == "message_accepted"
    assert db.rows[0]["run_id"] == "run-1"
    assert db.rows[0]["trigger_source"] == "chat"
    assert db.rows[0]["latency_ms"] == 123


@pytest.mark.asyncio
async def test_frontend_route_derives_identity_and_rejects_unknown_event(monkeypatch):
    from fastapi import HTTPException
    from backend.routes import analytics as route

    capture = AsyncMock()
    monkeypatch.setattr(route, "track", capture)
    monkeypatch.setattr(
        route, "resolve_current_user_id", AsyncMock(return_value="user-1")
    )
    request = SimpleNamespace(state=SimpleNamespace(user_id="user-1"))

    response = await route.capture_product_event(
        route.ProductEventRequest(
            event="reply_rendered",
            event_id="event-fixed-2",
            run_id="run-2",
            agent_id="agent-1",
        ),
        request,
    )
    assert response == {"success": True}
    assert capture.await_args.kwargs["user_id"] == "user-1"
    assert capture.await_args.kwargs["properties"]["source"] == "frontend"
    persisted_id = capture.await_args.kwargs["event_id"]
    assert persisted_id.startswith("fe:")
    assert persisted_id != "event-fixed-2"

    with pytest.raises(HTTPException) as exc:
        await route.capture_product_event(
            route.ProductEventRequest(event="arbitrary", event_id="event-fixed-3"),
            request,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_frontend_route_rate_limits_per_authenticated_user(monkeypatch):
    from fastapi import HTTPException
    from backend.routes import analytics as route
    from backend.routes._rate_limiter import SlidingWindowRateLimiter

    monkeypatch.setattr(route, "track", AsyncMock())
    monkeypatch.setattr(
        route, "resolve_current_user_id", AsyncMock(return_value="user-1")
    )
    monkeypatch.setattr(
        route, "_event_limiter", SlidingWindowRateLimiter(limit=1, window_sec=60)
    )
    request = SimpleNamespace(state=SimpleNamespace(user_id="user-1"))
    payload = route.ProductEventRequest(
        event="reply_rendered", event_id="event-fixed-rate"
    )

    assert await route.capture_product_event(payload, request) == {"success": True}
    with pytest.raises(HTTPException) as exc:
        await route.capture_product_event(payload, request)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_track_never_raises_when_persistence_fails(monkeypatch):
    import narranexus.platform.analytics as analytics

    monkeypatch.setattr(analytics, "_opted_out", AsyncMock(return_value=False))
    monkeypatch.setattr(
        analytics,
        "_persist_product_event",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    await analytics.track(user_id="user-1", event="workspace_ready")


@pytest.mark.asyncio
async def test_websocket_capture_helpers_use_stable_stage_ids(monkeypatch):
    from backend.routes import websocket as route

    capture = AsyncMock()
    monkeypatch.setattr(route, "track", capture)

    await route._record_message_accepted(
        user_id="user-1",
        agent_id="agent-1",
        trigger_source="chat",
        session_id="session-1",
    )
    accepted = capture.await_args.kwargs
    assert accepted["event"] == "message_accepted"
    assert accepted["event_id"] == "message_accepted:session-1"
    assert accepted["properties"]["session_id"] == "session-1"

    await route._record_run_started(
        user_id="user-1",
        agent_id="agent-1",
        run_id="run-1",
        trigger_source="chat",
        session_id="session-1",
    )
    started = capture.await_args.kwargs
    assert started["event"] == "run_started"
    assert started["event_id"] == "run_started:run-1"
    assert started["properties"]["run_id"] == "run-1"


def test_schema_registers_product_events_and_exact_provider_source():
    from narranexus.platform.utils.db.schema_registry import TABLES

    product = TABLES["product_analytics_events"]
    assert {column.name for column in product.columns} >= {
        "analytics_event_id", "event_name", "user_id", "failure_category",
        "provider_card_source", "occurred_at",
    }
    cost = TABLES["cost_records"]
    assert "provider_card_source" in {column.name for column in cost.columns}
    assert "idx_cost_provider_card_created" not in {
        index.name for index in cost.indexes
    }


def test_analytics_route_bypasses_quota_resolver():
    from backend.auth import QUOTA_BYPASS_PREFIXES

    assert "/api/analytics" in QUOTA_BYPASS_PREFIXES


def test_provider_card_source_is_selected_by_call_type():
    from narranexus.platform.agent_framework.api_config import (
        get_provider_card_source,
        set_provider_card_sources,
    )

    set_provider_card_sources({
        "agent": "netmind_free",
        "helper_llm": "openai",
    })
    assert get_provider_card_source("agent_loop") == "netmind_free"
    assert get_provider_card_source("llm_function") == "openai"


def test_message_failure_categories_are_normalized():
    from narranexus.platform.agent_runtime.background_run import _failure_category
    from narranexus.platform.agent_runtime.run_recorder import (
        STATE_CANCELLED,
        STATE_COMPLETED,
    )

    assert _failure_category("free_tier_exhausted", STATE_COMPLETED) == "quota"
    assert _failure_category("invalid_credentials", STATE_COMPLETED) == "auth"
    assert _failure_category("context_window", STATE_COMPLETED) == "configuration"
    assert _failure_category("executor_oom", STATE_COMPLETED) == "infrastructure"
    assert _failure_category("anything_else", STATE_COMPLETED) == "runtime"
    assert _failure_category(None, STATE_CANCELLED) == "cancelled"
