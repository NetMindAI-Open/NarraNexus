"""
@file_name: test_lark_trigger_audit.py
@author: Bin Liang
@date: 2026-04-21
@description: LarkTriggerAuditRepository tests.

The audit repository is the logbook the trigger writes into so that a
post-incident reviewer can reconstruct what happened without EC2 log
access. Every interesting lifecycle event — message ingress, dedup
decisions, WebSocket connect/disconnect, worker errors, timeouts,
heartbeats — lands in one row. These tests pin the shape of that log
so downstream consumers (the /healthz endpoint, future frontend pages)
can rely on it.
"""
from __future__ import annotations

import json

import pytest

from xyz_agent_context.repository.lark_trigger_audit_repository import (
    LarkTriggerAuditRepository,
    EVENT_INGRESS_PROCESSED,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_WS_CONNECTED,
    EVENT_WS_DISCONNECTED,
    EVENT_WORKER_ERROR,
    EVENT_HEARTBEAT,
)


@pytest.mark.asyncio
async def test_append_persists_core_fields(db_client):
    """An audit row round-trips its message_id/agent_id/chat_id through DB."""
    repo = LarkTriggerAuditRepository(db_client)

    await repo.append(
        EVENT_INGRESS_PROCESSED,
        message_id="om_1",
        agent_id="agent_a",
        app_id="cli_x",
        chat_id="oc_1",
        sender_id="ou_alice",
        details={"dedup_layer": "db", "enqueue_latency_ms": 3},
    )

    rows = await db_client.get("lark_trigger_audit", {})
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == EVENT_INGRESS_PROCESSED
    assert row["message_id"] == "om_1"
    assert row["agent_id"] == "agent_a"
    assert row["app_id"] == "cli_x"
    assert row["chat_id"] == "oc_1"
    assert row["sender_id"] == "ou_alice"
    # details is JSON-encoded so future fields can be added without migration
    details = json.loads(row["details"])
    assert details["dedup_layer"] == "db"
    assert details["enqueue_latency_ms"] == 3


@pytest.mark.asyncio
async def test_append_tolerates_missing_optional_fields(db_client):
    """Many lifecycle events (heartbeat, ws_connected) have no msg_id/chat_id."""
    repo = LarkTriggerAuditRepository(db_client)

    await repo.append(EVENT_HEARTBEAT, details={"queue_depth": 0, "worker_count": 3})

    rows = await db_client.get("lark_trigger_audit", {})
    assert len(rows) == 1
    # Empty or NULL — the column MUST be nullable so the insert doesn't fail
    assert not rows[0].get("message_id")
    assert not rows[0].get("agent_id")


@pytest.mark.asyncio
async def test_append_never_raises_on_backend_error(db_client, monkeypatch):
    """Audit writes are best-effort: if the DB hiccups, the trigger's hot
    path must not propagate the exception. Losing an audit row is always
    preferable to breaking real user-visible work."""
    repo = LarkTriggerAuditRepository(db_client)

    async def _boom(*_a, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_client, "insert", _boom)

    # Must not raise
    await repo.append(EVENT_INGRESS_PROCESSED, message_id="om_2")


@pytest.mark.asyncio
async def test_recent_returns_newest_first(db_client):
    """/healthz needs the most recent N rows; order must be newest-first."""
    repo = LarkTriggerAuditRepository(db_client)

    await repo.append(EVENT_WS_CONNECTED, agent_id="a1")
    await repo.append(EVENT_WS_DISCONNECTED, agent_id="a1", details={"uptime_s": 42})
    await repo.append(EVENT_INGRESS_PROCESSED, message_id="om_a", agent_id="a1")

    rows = await repo.recent(limit=10)
    assert [r["event_type"] for r in rows] == [
        EVENT_INGRESS_PROCESSED,
        EVENT_WS_DISCONNECTED,
        EVENT_WS_CONNECTED,
    ]


@pytest.mark.asyncio
async def test_recent_filters_by_event_type(db_client):
    repo = LarkTriggerAuditRepository(db_client)

    await repo.append(EVENT_INGRESS_PROCESSED, message_id="om_a")
    await repo.append(EVENT_WORKER_ERROR, message_id="om_b", details={"err": "x"})
    await repo.append(EVENT_INGRESS_PROCESSED, message_id="om_c")

    only_errors = await repo.recent(limit=10, event_type=EVENT_WORKER_ERROR)
    assert len(only_errors) == 1
    assert only_errors[0]["message_id"] == "om_b"


@pytest.mark.asyncio
async def test_recent_respects_limit(db_client):
    repo = LarkTriggerAuditRepository(db_client)
    for i in range(7):
        await repo.append(EVENT_HEARTBEAT, details={"i": i})

    rows = await repo.recent(limit=3)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_count_by_type_since_hours(db_client):
    """For /healthz we need a quick "in the last hour, how many of each
    event happened" summary."""
    repo = LarkTriggerAuditRepository(db_client)

    await repo.append(EVENT_INGRESS_PROCESSED, message_id="om_a")
    await repo.append(EVENT_INGRESS_PROCESSED, message_id="om_b")
    await repo.append(EVENT_WORKER_ERROR, message_id="om_c", details={})
    await repo.append(EVENT_WS_DISCONNECTED)

    counts = await repo.count_by_type(since_hours=1)
    assert counts[EVENT_INGRESS_PROCESSED] == 2
    assert counts[EVENT_WORKER_ERROR] == 1
    assert counts[EVENT_WS_DISCONNECTED] == 1


@pytest.mark.asyncio
async def test_cleanup_older_than_days_removes_aged_rows(db_client):
    """Retention is 30 days for audit — longer than lark_seen_messages'
    7 because post-incident review needs more history."""
    repo = LarkTriggerAuditRepository(db_client)

    await repo.append(EVENT_HEARTBEAT, details={"marker": "old"})
    await repo.append(EVENT_HEARTBEAT, details={"marker": "new"})

    # Age the first row directly in DB
    rows = await db_client.get("lark_trigger_audit", {})
    old_row = next(r for r in rows if '"old"' in r["details"])
    await db_client.update(
        "lark_trigger_audit",
        {"id": old_row["id"]},
        {"event_time": "2020-01-01 00:00:00.000000"},
    )

    deleted = await repo.cleanup_older_than_days(30)
    assert deleted >= 1

    remaining = await db_client.get("lark_trigger_audit", {})
    assert len(remaining) == 1
    assert '"new"' in remaining[0]["details"]
