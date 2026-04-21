"""
@file_name: test_lark_audit_trail.py
@author: Bin Liang
@date: 2026-04-21
@description: End-to-end verification that the audit log captures the
key decision points a post-incident reviewer cares about.

We don't start a real WebSocket — we drive the classifier and worker
paths directly and verify that the right rows land in
`lark_trigger_audit`.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger
from xyz_agent_context.repository.lark_seen_message_repository import (
    LarkSeenMessageRepository,
)
from xyz_agent_context.repository.lark_trigger_audit_repository import (
    LarkTriggerAuditRepository,
    EVENT_INGRESS_PROCESSED,
    EVENT_INGRESS_DROPPED_HISTORIC,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_INGRESS_DROPPED_UNBOUND,
    EVENT_INBOX_WRITE_FAILED,
    EVENT_SUBSCRIBER_STOPPED,
)


def _make_trigger(db_client) -> LarkTrigger:
    t = LarkTrigger()
    t.running = True
    t._startup_time_ms = int(time.time() * 1000)
    t._seen_repo = LarkSeenMessageRepository(db_client)
    t._audit_repo = LarkTriggerAuditRepository(db_client)
    return t


class _Cred:
    def __init__(self, agent_id="a1", app_id="cli_x"):
        self.agent_id = agent_id
        self.app_id = app_id
        self.profile_name = f"p_{agent_id}"
        self.brand = "lark"


@pytest.mark.asyncio
async def test_audit_records_accept_on_fresh_event(db_client):
    t = _make_trigger(db_client)
    cred = _Cred()

    event = {
        "message_id": "om_fresh",
        "create_time": str(t._startup_time_ms + 1),
        "chat_id": "oc_1",
        "sender_id": "ou_alice",
    }
    await t._dedup_and_enqueue(cred, event)

    rows = await t._audit_repo.recent(limit=5)
    types = [r["event_type"] for r in rows]
    assert EVENT_INGRESS_PROCESSED in types


@pytest.mark.asyncio
async def test_audit_records_historic_drop(db_client):
    t = _make_trigger(db_client)
    cred = _Cred()

    stale = t._startup_time_ms - t.HISTORY_BUFFER_MS - 60_000
    event = {
        "message_id": "om_stale",
        "create_time": str(stale),
        "chat_id": "oc_1",
    }
    await t._dedup_and_enqueue(cred, event)

    rows = await t._audit_repo.recent(limit=5)
    types = [r["event_type"] for r in rows]
    assert EVENT_INGRESS_DROPPED_HISTORIC in types


@pytest.mark.asyncio
async def test_audit_records_memory_dedup(db_client):
    t = _make_trigger(db_client)
    cred = _Cred()

    event = {
        "message_id": "om_dup",
        "create_time": str(t._startup_time_ms + 1),
        "chat_id": "oc_1",
    }
    await t._dedup_and_enqueue(cred, event)
    await t._dedup_and_enqueue(cred, event)  # second time = dup

    rows = await t._audit_repo.recent(limit=10)
    types = [r["event_type"] for r in rows]
    assert EVENT_INGRESS_DROPPED_DEDUP in types


@pytest.mark.asyncio
async def test_audit_records_unbound_drop(db_client, monkeypatch):
    t = _make_trigger(db_client)
    cred = _Cred(app_id="cli_unbound")
    # NOT registered in _subscriber_creds — gatekeeper must drop it

    monkeypatch.setattr(t, "_build_and_run_agent", AsyncMock())
    monkeypatch.setattr(t, "_write_to_inbox", AsyncMock())

    event = {"message_id": "om_u", "chat_id": "oc", "content": "hi",
             "sender_id": "ou_x", "sender_type": "user"}
    await t._process_message(cred, event, worker_id=0)

    rows = await t._audit_repo.recent(limit=5)
    types = [r["event_type"] for r in rows]
    assert EVENT_INGRESS_DROPPED_UNBOUND in types


@pytest.mark.asyncio
async def test_audit_records_inbox_write_failure(db_client, monkeypatch):
    """When _write_to_inbox hits a DB error, the content must not be
    silently lost — an audit row captures the original + response."""
    t = _make_trigger(db_client)
    cred = _Cred()

    async def _boom():
        raise RuntimeError("inbox db down")

    # get_db_client resolves inside _write_to_inbox; patch it
    from xyz_agent_context.module.lark_module import lark_trigger as lt_mod
    monkeypatch.setattr(lt_mod, "get_db_client", _boom)

    await t._write_to_inbox(
        cred=cred,
        sender_name="Alice",
        sender_id="ou_alice",
        original_message="hello",
        agent_response="world",
        chat_id="oc_1",
    )

    rows = await t._audit_repo.recent(limit=5)
    assert any(r["event_type"] == EVENT_INBOX_WRITE_FAILED for r in rows)


@pytest.mark.asyncio
async def test_audit_records_subscriber_stopped(db_client):
    t = _make_trigger(db_client)
    cred = _Cred()
    t._subscriber_creds[cred.app_id] = cred

    await t._stop_subscriber(cred.app_id)

    rows = await t._audit_repo.recent(limit=5)
    types = [r["event_type"] for r in rows]
    assert EVENT_SUBSCRIBER_STOPPED in types
