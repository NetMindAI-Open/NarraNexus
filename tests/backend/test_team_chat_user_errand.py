"""
@file_name: test_team_chat_user_errand.py
@author:
@date: 2026-08-17
@description: The user's own hand-offs reach the board too.

`message_bus/errand.py` records a hand-off when one agent @mentions another,
and it is wired into `MessageBusTrigger` — the path an AGENT's reply takes. A
person's message reaches the bus from this route instead, so "@Bruno pull the
numbers", ignored, left no trace anywhere: no board row, nothing for patrol to
sweep, nothing in the closure-rate report.

That is the one broken hand-off a person actually witnesses. An agent ignoring
another agent is invisible to them; being ignored themselves is not.

Driven over HTTP rather than by asserting on the route's source, for the reason
`test_team_chat_paging` records: a source assertion goes red on a rename and
green on a behavioural regression.
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX
from narranexus.platform.schema.team_work_schema import WorkItemOrigin

TEAM = "t_errand"
CHANNEL = "ch_errand"
OWNER = "usr_1"
BRUNO = "agent_bruno"
ANA = "agent_ana"


@pytest.fixture
def client(db_client, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from narranexus_plugins.teams import routes as mod

    async def _get_db():
        return db_client

    monkeypatch.setattr(mod, "get_db_client", _get_db)

    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(mod.router, prefix="/api/teams")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
async def room(db_client):
    await db_client.insert("teams", {
        "team_id": TEAM, "owner_user_id": OWNER, "name": "Desk",
        "lead_agent_id": ANA,
    })
    await db_client.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "Desk", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, name in ((ANA, "Ana"), (BRUNO, "Bruno")):
        await db_client.insert(
            "agents", {"agent_id": aid, "agent_name": name, "created_by": OWNER}
        )
        await db_client.insert(
            "bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid}
        )
        await db_client.insert("team_members", {"team_id": TEAM, "agent_id": aid})
    return LocalMessageBus(backend=db_client._backend)


def _post(client, content, mentions=None):
    return client.post(
        f"/api/teams/{TEAM}/chat/messages",
        json={"content": content, "mentions": mentions or []},
        headers={"X-User-Id": OWNER},
    )


@pytest.mark.asyncio
async def test_a_person_handing_work_to_an_agent_opens_an_errand(
    db_client, client, room
):
    r = _post(client, "@Bruno pull the Q3 numbers", mentions=[BRUNO])
    assert r.status_code == 200, r.text

    items = await TeamWorkItemRepository(db_client).list_active(TEAM)
    assert [i.assignee_id for i in items] == [BRUNO]
    assert items[0].origin == WorkItemOrigin.AUTO
    # Credited to the person, not to an agent — the board says who is owed.
    assert items[0].created_by.startswith("usr_")
    assert items[0].source_message_id == r.json()["message_id"]


@pytest.mark.asyncio
async def test_a_message_addressed_to_nobody_opens_nothing(db_client, client, room):
    """No @mention routes to the default responder so the room never goes
    silent — but the platform picking someone to answer is not the user handing
    them work, and a board row would claim otherwise."""
    assert _post(client, "how is it going?").status_code == 200

    assert await TeamWorkItemRepository(db_client).list_active(TEAM) == []


@pytest.mark.asyncio
async def test_addressing_the_room_opens_nothing(db_client, client, room):
    """`@all` addresses a room, not a person; nobody is late on it, and one row
    per member would flood the board every standup."""
    assert _post(client, "standup in 5", mentions=["@all"]).status_code == 200

    assert await TeamWorkItemRepository(db_client).list_active(TEAM) == []


@pytest.mark.asyncio
async def test_an_attachment_only_hand_off_gets_a_title_that_says_so(
    db_client, client, room, monkeypatch
):
    """This route is the ONLY entrance that allows an empty body.

    An agent's reply always carries text, so the shared "(untitled hand-off)"
    fallback in `_title_from` would surface here and nowhere else — and the
    board is read by every member every turn, so a row naming nothing costs
    tokens to skip. Only the route knows an attachment is what was handed over.

    The attachment is faked at the sanitiser rather than staged on disk: what
    is under test is the TITLE, and `_sanitized_attachment`'s own job (rebuild
    from server-side state, never trust the echo) has its own coverage.
    """
    from narranexus_plugins.teams import routes as mod

    monkeypatch.setattr(
        mod, "_sanitized_attachment",
        lambda _uid, _att: {"rel_path": "x.png", "mime": "image/png"},
    )

    r = client.post(
        f"/api/teams/{TEAM}/chat/messages",
        json={"content": "", "mentions": [BRUNO],
              "attachments": [{"rel_path": "x.png"}]},
        headers={"X-User-Id": OWNER},
    )
    assert r.status_code == 200, r.text

    items = await TeamWorkItemRepository(db_client).list_active(TEAM)
    assert len(items) == 1
    assert "attachment" in items[0].title
    assert "untitled" not in items[0].title


@pytest.mark.asyncio
async def test_book_keeping_never_costs_the_user_their_message(
    db_client, client, room, monkeypatch
):
    """The message is already in the room by the time the board is touched.

    Failing the request would trade a delivered message for a bookkeeping row,
    and the user would retype something everyone can already see.
    """
    async def _boom(*_a, **_k):
        raise RuntimeError("board is on fire")

    monkeypatch.setattr(
        "narranexus.platform.message_bus.errand.record_handoffs", _boom
    )

    r = _post(client, "@Bruno pull the Q3 numbers", mentions=[BRUNO])

    assert r.status_code == 200, r.text
    rows = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    assert [m["content"] for m in rows] == ["@Bruno pull the Q3 numbers"]


@pytest.mark.asyncio
async def test_the_room_poll_carries_the_patrol_switch(db_client, client, room):
    """2026-09-03: one feed for the switch, so no panel pulls the board for it.

    Defaults ON for a team with a lead (`patrol_is_on`); the PUT flips it and
    the next poll says so.
    """
    r = client.get(f"/api/teams/{TEAM}/chat/messages", headers={"X-User-Id": OWNER})
    assert r.status_code == 200
    assert r.json()["patrol_enabled"] is True

    off = client.put(
        f"/api/teams/{TEAM}/patrol", json={"enabled": False}, headers={"X-User-Id": OWNER},
    )
    assert off.status_code == 200
    r = client.get(f"/api/teams/{TEAM}/chat/messages", headers={"X-User-Id": OWNER})
    assert r.json()["patrol_enabled"] is False
