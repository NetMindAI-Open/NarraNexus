"""
@file_name: test_team_rename_room.py
@author:
@date: 2026-08-12
@description: A renamed team is renamed everywhere its agents can see.

The room carries its own copy of the name, and that copy is the one agents
read — `Your Channels` renders `bus_channels.name`. A rename that stopped at
the teams table left every member citing the old name back at the user while
the UI showed the new one, with nothing in either place to explain the
disagreement.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_renaming_a_team_renames_the_room_agents_see(db_client, monkeypatch):
    """The room keeps its own copy of the name, and that copy is the one agents
    read: `Your Channels` renders `bus_channels.name`. A rename that stopped at
    the teams table left every member citing the old name back at the user while
    the UI showed the new one — a disagreement neither side could explain.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import narranexus_plugins.teams.routes as teams_mod

    async def _db():
        return db_client

    async def _uid(_request):
        return "usr_1"

    monkeypatch.setattr(teams_mod, "get_db_client", _db)
    monkeypatch.setattr(teams_mod, "_user_id_for_request", _uid)

    await db_client.insert("teams", {
        "team_id": "t1", "owner_user_id": "usr_1", "name": "Old Desk",
    })
    await db_client.insert("bus_channels", {
        "channel_id": "ch_1", "name": "Old Desk", "channel_type": "group",
        "created_by": "team_t1",
    })

    app = FastAPI()
    app.include_router(teams_mod.router, prefix="/api/teams")
    r = TestClient(app).patch("/api/teams/t1", json={"name": "New Desk"})

    assert r.status_code == 200
    room = await db_client.get_one("bus_channels", {"channel_id": "ch_1"})
    assert room["name"] == "New Desk"
