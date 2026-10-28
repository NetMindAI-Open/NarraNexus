"""
@file_name: test_activity_log_a2a_visibility.py
@author:
@date: 2026-08-21
@description: The Activity Log (simple-chat-history, include=activity) must
surface the agent's A2A / team activity to its OWNER, must NOT leak it to anyone
else or leak any other user's chat, and must NOT starve the conversation stream.

A2A and team turns run through MessageBusTrigger, which invokes the runtime with
``user_id = sender_agent_id`` (the peer agent id for A2A, ``team_<id>`` for a
team room — not the owner). Their turns are therefore stored in ChatModule
instances keyed to that peer/team id, which the owner-scoped query never
returns, so the Activity Log showed none of the agent's peer/team activity.

The fix pulls the agent's peer-scoped ChatModule instances too, but ONLY for the
owner, ONLY instances whose user_id is a peer/team scope (prefix), and surfaces
ONLY their background a2a / message_bus rows (collapsed to a compact activity
marker). The conversation and activity streams paginate independently via the
``include`` param so a flood of peer activity cannot empty the conversation tab.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import narranexus_plugins.chat_module.routes as ch

OWNER_ID = "owner_u1"
AGENT_ID = "agent_mine"
PEER_REPLY = "SECRET peer-to-peer content"
TEAM_REPLY = "SECRET team-room content"
OWNER_NOTIFY_TEXT = "IMPORTANT the deploy finished, owner should see this"


def _msg(role, content, ts, source=None, eid=None, notify=None):
    meta = {"timestamp": ts}
    if source:
        meta["working_source"] = source
    if eid:
        meta["event_id"] = eid
    if notify is not None:
        meta["owner_notify_content"] = notify
    return {"role": role, "content": content, "meta_data": meta}


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

    await db.insert("agents", {
        "agent_id": AGENT_ID, "agent_name": "Daolai",
        "created_by": OWNER_ID, "agent_type": "chat",
    })

    async def _instance(iid, uid, *msgs):
        await db.insert("module_instances", {
            "instance_id": iid, "agent_id": AGENT_ID, "user_id": uid,
            "module_class": "ChatModule", "status": "active",
        })
        await db.insert("instance_json_format_memory_chat",
                        {"instance_id": iid, "memory": _memory(*msgs)})

    # Owner's own instance: one conversation turn (early) PLUS a flood of the
    # owner's own background activity (job-source assistant rows, collapsed to
    # message_type == "activity"), all LATER than the conversation. These are
    # owner-scoped so they land in every include and let the conversation-stream
    # starvation test bite without needing the peer pull.
    owner_msgs = [
        _msg("user", "hi mine", "2026-08-21T06:00:00"),
        _msg("assistant", "hello owner", "2026-08-21T06:00:01"),
    ]
    owner_msgs += [
        _msg("assistant", f"ran job #{i}", f"2026-08-21T09:{i:02d}:00", "job")
        for i in range(30)
    ]
    await _instance("chat_owner", OWNER_ID, *owner_msgs)

    # A2A peer instance: a user-facing prompt row + assistant replies, keyed to
    # the peer AGENT id. The replies are stored as message_bus turns (LATER than
    # the owner chat, to exercise starvation).
    peer_msgs = [_msg("user", "peer prompt", "2026-08-21T07:00:00")]
    peer_msgs += [
        _msg("assistant", f"{PEER_REPLY} #{i}", f"2026-08-21T07:{i:02d}:00", "message_bus")
        for i in range(30)
    ]
    await _instance("chat_peer", "agent_peer", *peer_msgs)

    # Team-room instance (team_ prefix), assistant reply stored as an a2a turn —
    # covers the OTHER _A2A_TEAM_SOURCES value and the team prefix.
    await _instance("chat_team", "team_room1",
                    _msg("assistant", TEAM_REPLY, "2026-08-21T08:00:00", "a2a"))

    # A HUMAN speaking in a team room: teams.py sends from_agent="usr_<uid>",
    # so the turn lands in an instance keyed to a usr_ id (NOT the owner). This
    # is the most common team path; the peer-scope prefix set must include it.
    # Tagged with a unique event_id so the test can assert THIS instance made it
    # through (content collapses to the generic activity marker).
    await _instance("chat_user_room", "usr_teamspeaker",
                    _msg("assistant", "human-room reply", "2026-08-21T08:30:00",
                         "message_bus", eid="evt_userroom"))

    # A peer/team turn where the agent explicitly called
    # send_message_to_user_directly (owner-notify carve-out): the row carries
    # owner_notify_content. It must reach the OWNER — verbatim — in the Activity
    # Log (peer scope → marked activity, NOT collapsed), and must NOT appear in
    # the conversation tab (which never reads peer instances).
    await _instance("chat_notify", "agent_notifier",
                    _msg("assistant", "raw peer chatter not for owner",
                         "2026-08-21T08:45:00", "message_bus",
                         notify=OWNER_NOTIFY_TEXT))

    # An unrelated third HUMAN user's private chat with the same agent. Its
    # user_id is not a peer/team prefix, so it must never be pulled at all.
    await _instance("chat_intruder", "intruder_u2",
                    _msg("user", "intruder hi", "2026-08-21T06:30:00"),
                    _msg("assistant", "hello intruder", "2026-08-21T06:30:01"))

    async def _db():
        return db

    monkeypatch.setattr(ch, "get_db_client", _db)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(ch.router, prefix="/api/agents")
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        await db.close()


def _history(client, user, include="all", limit=50):
    r = client.get(f"/api/agents/{AGENT_ID}/simple-chat-history",
                   params={"limit": limit, "include": include},
                   headers={"x-test-user": user})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    return body["messages"], r.text


def test_owner_activity_stream_shows_peer_and_team(client):
    msgs, raw = _history(client, OWNER_ID, include="activity", limit=500)
    sources = {m.get("working_source") for m in msgs}
    # Non-vacuous: without the peer pull neither appears. Both bus sources land.
    assert "message_bus" in sources, "A2A peer activity missing"
    assert "a2a" in sources, "team-room (a2a) activity missing"
    # The human-in-team-room turn (usr_ scope) must be surfaced — this is the
    # most common team path and the prefix whitelist must include usr_.
    assert any(m.get("event_id") == "evt_userroom" for m in msgs), \
        "human team-room (usr_) activity missing — prefix whitelist too narrow"
    assert all(m.get("message_type") == "activity" for m in msgs)
    # Raw peer/team reply text is collapsed, never surfaced verbatim.
    assert PEER_REPLY not in raw and TEAM_REPLY not in raw


def test_owner_isolation_drops_peer_userfacing_and_other_users(client):
    # include=all returns conversation rows too, so a leaked peer/other-user
    # row WOULD show up here. Pins two nets: the peer-instance user_id prefix
    # filter (intruder never pulled) and the row-level working_source guard
    # (peer instance's own user-facing "peer prompt" dropped).
    # High limit so the assertions cover EVERY row (leak checks, not paging).
    msgs, raw = _history(client, OWNER_ID, include="all", limit=500)
    assert "peer prompt" not in raw          # peer instance user-facing row
    assert "intruder hi" not in raw          # other human's chat (prefix-filtered)
    assert "hello intruder" not in raw
    assert PEER_REPLY not in raw and TEAM_REPLY not in raw
    assert any(m.get("content") == "hi mine" for m in msgs)  # owner's own chat


def test_owner_notify_on_peer_turn_reaches_activity_verbatim(client):
    # The agent's send_message_to_user_directly report on a peer/team turn must
    # reach the owner VERBATIM in the Activity Log (peer scope → marked
    # activity, not collapsed), and must NOT surface in the conversation tab.
    act, act_raw = _history(client, OWNER_ID, include="activity", limit=500)
    notify_rows = [m for m in act if m.get("content") == OWNER_NOTIFY_TEXT]
    # Non-vacuous: without the peer_scoped message_type override this row stays
    # message_type=='chat' and the activity stream filters it out → empty.
    assert notify_rows, "owner-notify on a peer turn is invisible in the Activity Log"
    assert all(m.get("message_type") == "activity" for m in notify_rows)
    # The row's OWN raw chatter (not the notify) is never surfaced.
    assert "raw peer chatter not for owner" not in act_raw

    chat, _ = _history(client, OWNER_ID, include="chat", limit=500)
    assert not any(m.get("content") == OWNER_NOTIFY_TEXT for m in chat), \
        "peer owner-notify must not leak into the conversation tab"


def test_non_owner_never_sees_peer_activity(client):
    msgs, raw = _history(client, "intruder_u2", include="all", limit=500)
    assert not any(m.get("working_source") in ("a2a", "message_bus") for m in msgs)
    assert PEER_REPLY not in raw and TEAM_REPLY not in raw
    assert any(m.get("content") == "intruder hi" for m in msgs)  # own chat intact


def test_chat_stream_not_starved_by_activity_flood(client):
    # 30 owner-scoped activity rows (job source) are all NEWER than the owner's
    # one conversation turn. A shared 20-row budget would return all-activity
    # and empty the conversation tab; include='chat' must slice its OWN stream
    # so the conversation turn survives. Non-vacuous: drop the include split and
    # the last-20 becomes the job flood, and "hi mine" disappears.
    msgs, _ = _history(client, OWNER_ID, include="chat", limit=20)
    assert any(m.get("content") == "hi mine" for m in msgs), \
        "conversation stream starved by the activity flood"
    assert all(m.get("message_type") != "activity" for m in msgs)
