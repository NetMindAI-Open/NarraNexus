"""
@file_name: test_agent_dm_inbox.py
@date: 2026-08-20
@description: An agent-to-agent DM has to reach BOTH agents' Agent Inbox.

The 2026-08-17 refactor re-pointed the inbox panel to `inbox_threads` /
`inbox_thread_messages` and rewired the IM triggers to write them, but the
agent-to-agent (peer DM) path was never given a writer. A2A messages landed
only in `bus_messages` (+ the separate owner-notification `inbox_table`), so
`GET /api/agent-inbox?agent_id=` found no thread and the panel was empty even
after agents talked.

The recording lives at the SEND site (`message_agent` tool), not the delivery
side, because that is the only place holding the text the peer actually
received: on a peer DM the sender's `turn.text` is a monologue to its OWNER, and
the peer is reached exclusively by the bus send tool. So the send records the
sender's OUTBOUND (its own thread) and the recipient's INBOUND (their thread) in
one shot — each thread shows the full round-trip.
"""
from __future__ import annotations

import json

import pytest

from narranexus.platform.channel.inbox_recorder import (
    InboxRecorder,
    agent_dm_thread_id,
)
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
    register_message_bus_mcp_tools,
)

OWNER = "usr_dm"
A, B = "agent_alice", "agent_bob"


async def _agent(db, agent_id, name, owner=OWNER):
    await db.insert(
        "agents", {"agent_id": agent_id, "agent_name": name, "created_by": owner}
    )


def _patch_db(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )
    # ServiceAuditor resolves its db via `narranexus.platform.utils.get_db_client`
    # (a separate binding from the db_factory one), so patch it too or the audit
    # rows would land in the real DB instead of the test one.
    monkeypatch.setattr("narranexus.platform.utils.get_db_client", _async_db)


def _tools(db_client):
    bus = LocalMessageBus(backend=db_client._backend)
    captured: dict = {}

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                captured[fn.__name__] = fn
                return fn

            return _wrap

    async def _bus():
        return bus

    register_message_bus_mcp_tools(_Stub(), _bus)
    return captured, bus


# ── the recorder itself ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_peer_message_writes_both_threads(db_client):
    await _agent(db_client, A, "Alice")
    await _agent(db_client, B, "Bob")

    await InboxRecorder("agent_dm", "Agent").record_peer_message(
        db=db_client, owner_user_id=OWNER,
        from_agent=A, from_name="Alice",
        to_agent=B, to_name="Bob",
        content="hey Bob, can you help?",
    )

    # Sender's thread: an OUTBOUND row.
    a_thread = agent_dm_thread_id(A, B)
    a_row = await db_client.get_one("inbox_threads", {"thread_id": a_thread})
    assert a_row is not None and a_row["agent_id"] == A
    assert a_row["source"] == "agent_dm" and a_row["counterpart_id"] == B
    a_msgs = await db_client.get("inbox_thread_messages", {"thread_id": a_thread})
    assert [(m["direction"], m["content"]) for m in a_msgs] == [
        ("out", "hey Bob, can you help?")
    ]

    # Recipient's thread: an INBOUND row from the sender.
    b_thread = agent_dm_thread_id(B, A)
    b_row = await db_client.get_one("inbox_threads", {"thread_id": b_thread})
    assert b_row is not None and b_row["agent_id"] == B
    assert b_row["counterpart_id"] == A
    b_msgs = await db_client.get("inbox_thread_messages", {"thread_id": b_thread})
    assert [(m["direction"], m["content"]) for m in b_msgs] == [
        ("in", "hey Bob, can you help?")
    ]


@pytest.mark.asyncio
async def test_record_peer_message_skips_empty(db_client):
    await _agent(db_client, A, "Alice")
    await _agent(db_client, B, "Bob")
    await InboxRecorder("agent_dm", "Agent").record_peer_message(
        db=db_client, owner_user_id=OWNER,
        from_agent=A, from_name="Alice", to_agent=B, to_name="Bob",
        content="   ",
    )
    assert await db_client.get("inbox_threads", {}) == []


# ── the wired path: the message_agent tool records on send ──────────────────

@pytest.mark.asyncio
async def test_message_agent_tool_fills_both_inboxes(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A, "Alice")
    await _agent(db_client, B, "Bob")
    captured, _ = _tools(db_client)

    res = await captured["message_agent"](agent_id=A, to=B, text="ping from Alice")
    assert res["success"], res

    # A's outbox and B's inbox both carry the actually-sent text.
    a_msgs = await db_client.get(
        "inbox_thread_messages", {"thread_id": agent_dm_thread_id(A, B)}
    )
    assert [(m["direction"], m["content"]) for m in a_msgs] == [
        ("out", "ping from Alice")
    ]
    b_msgs = await db_client.get(
        "inbox_thread_messages", {"thread_id": agent_dm_thread_id(B, A)}
    )
    assert [(m["direction"], m["content"]) for m in b_msgs] == [
        ("in", "ping from Alice")
    ]


@pytest.mark.asyncio
async def test_send_succeeds_and_audits_when_recording_fails(db_client, monkeypatch):
    """A recorder failure must NOT invert an already-delivered send, and it must
    leave a DB audit row (not just a log that rotates away)."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A, "Alice")
    await _agent(db_client, B, "Bob")
    captured, _ = _tools(db_client)

    async def _boom(*_a, **_k):
        raise RuntimeError("inbox table gone")

    monkeypatch.setattr(InboxRecorder, "record_peer_message", _boom)

    res = await captured["message_agent"](agent_id=A, to=B, text="still delivered")
    assert res["success"] is True, "a recorder failure inverted a delivered send"

    audit = await db_client.get(
        "service_audit",
        {"service": "message_bus_mcp", "event_type": "inbox_write_failed"},
    )
    assert len(audit) == 1, "the inbox write failure left no audit row"
    # The row has to be USABLE for triage: who → who, and what broke. A row
    # with no from/to is nearly as useless as no row.
    detail = json.loads(audit[0]["detail"])
    assert detail["from_agent"] == A
    assert detail["to_agent"] == B
    assert "RuntimeError" in detail["error"]


@pytest.mark.asyncio
async def test_send_succeeds_even_if_audit_write_also_raises(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A, "Alice")
    await _agent(db_client, B, "Bob")
    captured, _ = _tools(db_client)

    async def _boom(*_a, **_k):
        raise RuntimeError("inbox down")

    async def _audit_boom(*_a, **_k):
        raise RuntimeError("audit down too")

    monkeypatch.setattr(InboxRecorder, "record_peer_message", _boom)
    monkeypatch.setattr(
        "narranexus.platform.services.service_audit.ServiceAuditor.event", _audit_boom
    )

    res = await captured["message_agent"](agent_id=A, to=B, text="delivered anyway")
    assert res["success"] is True


@pytest.mark.asyncio
async def test_no_thread_when_owner_unresolved(db_client, monkeypatch):
    """An agent whose owner cannot be resolved must not pin a thread to owner=''."""
    _patch_db(monkeypatch, db_client)
    # created_by="" -> _resolve_owner_user_id returns "" -> skip recording.
    await _agent(db_client, A, "Alice", owner="")
    await _agent(db_client, B, "Bob", owner="")
    captured, _ = _tools(db_client)

    res = await captured["message_agent"](agent_id=A, to=B, text="no owner")
    assert res["success"] is True
    assert await db_client.get("inbox_threads", {}) == []


@pytest.mark.asyncio
async def test_no_thread_when_recipient_agent_missing(db_client, monkeypatch):
    """Sending to an invented id (not a real agent) must not create a phantom
    thread — the cross-user guard only blocks a KNOWN other-owner agent."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A, "Alice")
    captured, _ = _tools(db_client)

    res = await captured["message_agent"](agent_id=A, to="agent_ghost", text="hi?")
    assert res["success"] is True
    assert await db_client.get("inbox_threads", {}) == []


@pytest.mark.asyncio
async def test_round_trip_gives_each_thread_both_directions(db_client, monkeypatch):
    """A→B then B→A: each agent's own thread shows out AND in — the full DM."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, A, "Alice")
    await _agent(db_client, B, "Bob")
    captured, _ = _tools(db_client)

    await captured["message_agent"](agent_id=A, to=B, text="hi Bob")
    await captured["message_agent"](agent_id=B, to=A, text="hi Alice")

    a_dirs = {
        m["direction"]
        for m in await db_client.get(
            "inbox_thread_messages", {"thread_id": agent_dm_thread_id(A, B)}
        )
    }
    assert a_dirs == {"out", "in"}, "Alice's thread should show both halves"
    b_dirs = {
        m["direction"]
        for m in await db_client.get(
            "inbox_thread_messages", {"thread_id": agent_dm_thread_id(B, A)}
        )
    }
    assert b_dirs == {"out", "in"}, "Bob's thread should show both halves"
