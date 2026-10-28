"""
@file_name: test_team_chat_oversize.py
@date: 2026-09-10
@description: The team-chat HTTP route answers an oversize message with 400
and the bus's own reason, not a 500 from the write edge's ValueError
(review r2 I2). The at-limit message still lands.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_send_team_chat_refuses_an_oversize_message_with_400(db_client, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import narranexus_plugins.teams.routes as teams_mod
    from narranexus.platform.message_bus.multipart import MAX_BUS_MESSAGE_BYTES

    async def _db():
        return db_client

    async def _uid(_request):
        return "usr_owner"

    monkeypatch.setattr(teams_mod, "get_db_client", _db)
    monkeypatch.setattr(teams_mod, "_user_id_for_request", _uid)
    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _db)
    await db_client.insert("teams", {"team_id": "t1", "owner_user_id": "usr_owner", "name": "Desk"})
    await db_client.insert("agents", {"agent_id": "agent_t", "agent_name": "T", "created_by": "usr_owner"})
    await db_client.insert("team_members", {"team_id": "t1", "agent_id": "agent_t"})

    app = FastAPI()
    app.include_router(teams_mod.router, prefix="/api/teams")
    client = TestClient(app)

    r = client.post("/api/teams/t1/chat/messages", json={"content": "x" * (MAX_BUS_MESSAGE_BYTES + 1)})
    assert r.status_code == 400, r.text
    # A person is on this end: no tool parameters in the advice (#389 I1).
    detail = r.json()["detail"]
    assert "part_index" not in detail and "too long" in detail
    assert await db_client.get("bus_messages", {}) == []

    # Whitespace does not count (the route strips before sending), and the
    # limit itself is allowed.
    r = client.post("/api/teams/t1/chat/messages", json={"content": "  " + "x" * MAX_BUS_MESSAGE_BYTES + "  "})
    assert r.status_code == 200, r.text
