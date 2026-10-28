"""
@file_name: test_chat_history_seam_route.py
@author:
@date: 2026-08-10
@description: Route-level tests for the chat-history MCP data-access-seam
endpoint (POST /{agent_id}/chat-history/by-instance). Owner-gated byte-parity
twin of the get_chat_history tool: runs the SAME shared fetch_chat_history that
the seam's DirectStore runs, against a real in-memory sqlite, and asserts the
instance-scope closes the cross-agent IDOR the old tool had.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import narranexus_plugins.chat_module.routes as ch

OWNER_ID = "user_tc"
OWNER = {"x-test-user": OWNER_ID}


def _memory(*msgs):
    return json.dumps({"messages": list(msgs)})


@pytest.fixture
async def client(monkeypatch):
    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    backend_db = SQLiteBackend(":memory:")
    await backend_db.initialize()
    await auto_migrate(backend_db)
    db = await AsyncDatabaseClient.create_with_backend(backend_db)

    # agent_mine owns instance chat_mine; agent_theirs owns chat_theirs.
    for aid, iid in (("agent_mine", "chat_mine"), ("agent_theirs", "chat_theirs")):
        await db.insert("module_instances", {
            "instance_id": iid, "agent_id": aid, "user_id": OWNER_ID,
            "module_class": "ChatModule", "status": "active",
        })
    await db.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_mine",
        "memory": _memory({"role": "user", "content": "hi mine"},
                          {"role": "assistant", "content": "hello"}),
    })
    await db.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_theirs",
        "memory": _memory({"role": "user", "content": "SECRET of another agent"}),
    })

    async def _db():
        return db

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(ch, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": OWNER_ID, "agent_theirs": OWNER_ID}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(ch.router, prefix="/api/agents")
    try:
        yield TestClient(app)
    finally:
        await db.close()


def test_chat_history_non_owner_is_denied(client):
    r = client.post("/api/agents/agent_mine/chat-history/by-instance",
                    headers={"x-test-user": "someone_else"}, json={"instance_id": "chat_mine"})
    assert r.status_code == 403


def test_chat_history_own_instance_returns_messages(client):
    r = client.post("/api/agents/agent_mine/chat-history/by-instance",
                    headers=OWNER, json={"instance_id": "chat_mine"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["total_messages"] == 2
    assert body["messages"][0]["content"] == "hi mine"


def test_chat_history_foreign_instance_is_empty_no_leak(client):
    # agent_mine (owned by the caller) asking for chat_theirs (belongs to
    # agent_theirs) must get EMPTY history — the instance-scope closes the IDOR.
    # Non-vacuous: without the scope check the SECRET would come back.
    r = client.post("/api/agents/agent_mine/chat-history/by-instance",
                    headers=OWNER, json={"instance_id": "chat_theirs"})
    assert r.status_code == 200
    body = r.json()
    assert body["total_messages"] == 0 and body["messages"] == []
    assert "SECRET" not in r.text


def test_chat_history_empty_instance_id_is_empty_not_422(client):
    # Byte-parity: an empty instance_id must read as empty history (200), same as
    # DirectStore — NOT a 422. The route body deliberately has no min_length.
    r = client.post("/api/agents/agent_mine/chat-history/by-instance",
                    headers=OWNER, json={"instance_id": ""})
    assert r.status_code == 200
    assert r.json()["messages"] == []
