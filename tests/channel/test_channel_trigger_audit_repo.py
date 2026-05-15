"""
ChannelTriggerAuditRepository — per-channel audit log, never-raises append.
"""
from __future__ import annotations

import json

import pytest

from xyz_agent_context.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
    EVENT_INGRESS_PROCESSED,
    EVENT_HEARTBEAT,
)


@pytest.mark.asyncio
async def test_append_persists_core_fields_with_channel_column(db_client):
    repo = ChannelTriggerAuditRepository("slack", db_client)

    await repo.append(
        EVENT_INGRESS_PROCESSED,
        message_id="m_1",
        agent_id="agent_a",
        app_id="A0X",
        chat_id="C123",
        sender_id="U_alice",
        details={"dedup_layer": "db_new", "queue_depth": 2},
    )

    rows = await db_client.get("channel_trigger_audit", {})
    assert len(rows) == 1
    row = rows[0]
    assert row["channel"] == "slack"
    assert row["event_type"] == EVENT_INGRESS_PROCESSED
    assert row["message_id"] == "m_1"
    assert row["chat_id"] == "C123"
    details = json.loads(row["details"])
    assert details["dedup_layer"] == "db_new"


@pytest.mark.asyncio
async def test_append_tolerates_missing_optional_fields(db_client):
    """Heartbeat / lifecycle events have no msg/chat/sender."""
    repo = ChannelTriggerAuditRepository("lark", db_client)
    await repo.append(EVENT_HEARTBEAT, details={"queue_depth": 0, "worker_count": 3})
    rows = await db_client.get("channel_trigger_audit", {})
    assert len(rows) == 1
    assert not rows[0].get("message_id")
    assert not rows[0].get("agent_id")


@pytest.mark.asyncio
async def test_append_never_raises_on_backend_error(db_client, monkeypatch):
    """Audit writes are best-effort: a DB hiccup must NOT propagate."""
    repo = ChannelTriggerAuditRepository("slack", db_client)

    async def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db_client, "insert", boom)
    # Must not raise
    await repo.append(EVENT_HEARTBEAT, details={"queue_depth": 0})


@pytest.mark.asyncio
async def test_recent_filters_by_channel(db_client):
    lark = ChannelTriggerAuditRepository("lark", db_client)
    slack = ChannelTriggerAuditRepository("slack", db_client)
    await lark.append(EVENT_HEARTBEAT)
    await slack.append(EVENT_HEARTBEAT)
    await slack.append(EVENT_INGRESS_PROCESSED)

    slack_rows = await slack.recent(limit=10)
    assert len(slack_rows) == 2
    assert all(r["channel"] == "slack" for r in slack_rows)


@pytest.mark.asyncio
async def test_count_by_type_uses_channel_filter(db_client):
    lark = ChannelTriggerAuditRepository("lark", db_client)
    slack = ChannelTriggerAuditRepository("slack", db_client)
    await lark.append(EVENT_INGRESS_PROCESSED)
    await slack.append(EVENT_INGRESS_PROCESSED)
    await slack.append(EVENT_HEARTBEAT)

    counts = await slack.count_by_type(since_hours=24)
    assert counts == {EVENT_INGRESS_PROCESSED: 1, EVENT_HEARTBEAT: 1}


@pytest.mark.asyncio
async def test_cleanup_filters_by_channel(db_client):
    """Cleanup on channel A must not touch channel B."""
    import datetime as dt

    lark = ChannelTriggerAuditRepository("lark", db_client)
    slack = ChannelTriggerAuditRepository("slack", db_client)
    await lark.append(EVENT_HEARTBEAT)
    await slack.append(EVENT_HEARTBEAT)

    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).isoformat(sep=" ")
    rows = await db_client.get("channel_trigger_audit", {})
    for r in rows:
        await db_client.update(
            "channel_trigger_audit",
            {"id": r["id"]},
            {"event_time": old_ts},
        )

    deleted = await lark.cleanup_older_than_days(days=30)
    assert deleted == 1
    remaining = await db_client.get("channel_trigger_audit", {})
    assert len(remaining) == 1
    assert remaining[0]["channel"] == "slack"


def test_audit_repo_rejects_empty_channel():
    with pytest.raises(ValueError):
        ChannelTriggerAuditRepository("", None)
