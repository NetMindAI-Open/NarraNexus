"""
@file_name: test_notices_routes.py
@author: Bin Liang
@date: 2026-07-03
@description: Tests for the user-scope notices routes (inbox_table read side):
  GET  /api/notices
  POST /api/notices/{message_id}/read

Follow-up to upstream #52: MessageBusTrigger._notify_permanent_failure writes
SYSTEM_NOTICE rows into inbox_table so the owner learns their agent gave up on
a message — but until now NOTHING read that table (write-only data). These
routes are the read side; tenancy mirrors agents_bus_failures.py (viewer from
session, a foreign message 404s so existence isn't leaked).
"""
from __future__ import annotations

import pytest_asyncio
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.inbox_repository import InboxRepository
from xyz_agent_context.schema.inbox_schema import InboxMessageType
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.schema_registry import auto_migrate

import backend.routes.notices as notices_mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


def _build_client(db_client, viewer_id: str = "user_x"):
    app = FastAPI()
    app.include_router(notices_mod.router, prefix="/api/notices")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        return await call_next(request)

    async def _get_db_override():
        return db_client

    notices_mod.get_db_client = _get_db_override
    return TestClient(app)


async def _seed_notice(db_client, user_id="user_x", title="Agent gave up", n=1):
    repo = InboxRepository(db_client)
    ids = []
    for i in range(n):
        message_id = f"ibx_{user_id}_{i}_{title[:8].replace(' ', '_')}"
        await repo.create_message(
            user_id=user_id,
            title=f"{title} #{i}",
            content="Provider credential invalid; message parked after 3 retries.",
            message_id=message_id,
            message_type=InboxMessageType.SYSTEM_NOTICE,
        )
        ids.append(message_id)
    return ids


@pytest.mark.asyncio
async def test_list_returns_own_notices_with_unread_count(db_client):
    await _seed_notice(db_client, user_id="user_x", n=2)
    await _seed_notice(db_client, user_id="someone_else", n=1)
    client = _build_client(db_client, viewer_id="user_x")

    resp = client.get("/api/notices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["notices"]) == 2
    assert body["unread_count"] == 2
    first = body["notices"][0]
    assert first["message_type"] == "system"
    assert first["is_read"] is False
    assert "content" in first and "title" in first and "created_at" in first
    # No other user's rows may leak.
    assert all(n["title"].startswith("Agent gave up") for n in body["notices"])


@pytest.mark.asyncio
async def test_unread_only_filter(db_client):
    ids = await _seed_notice(db_client, n=2)
    repo = InboxRepository(db_client)
    await repo.mark_as_read(ids[0])
    client = _build_client(db_client)

    body = client.get("/api/notices", params={"unread_only": "true"}).json()
    assert len(body["notices"]) == 1
    assert body["unread_count"] == 1


@pytest.mark.asyncio
async def test_mark_read(db_client):
    ids = await _seed_notice(db_client, n=1)
    client = _build_client(db_client)

    resp = client.post(f"/api/notices/{ids[0]}/read")
    assert resp.status_code == 200
    body = client.get("/api/notices").json()
    assert body["unread_count"] == 0
    assert body["notices"][0]["is_read"] is True


@pytest.mark.asyncio
async def test_mark_read_foreign_message_is_404(db_client):
    ids = await _seed_notice(db_client, user_id="someone_else", n=1)
    client = _build_client(db_client, viewer_id="user_x")

    resp = client.post(f"/api/notices/{ids[0]}/read")
    assert resp.status_code == 404  # masks existence, same policy as bus-failures


@pytest.mark.asyncio
async def test_mark_read_unknown_message_is_404(db_client):
    client = _build_client(db_client)
    assert client.post("/api/notices/nope/read").status_code == 404
