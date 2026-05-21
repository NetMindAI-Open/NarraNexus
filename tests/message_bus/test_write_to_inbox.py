"""
@file_name: test_write_to_inbox.py
@author: Bin Liang
@date: 2026-05-19
@description: Regression test for `MessageBusTrigger._write_to_inbox`
crashing with `Unknown column 'agent_id' in 'field list'`.

Observed on EC2 bus container 2026-05-18T20:00:07 → 23:08:03 (13 hits).

Root cause: the original implementation hand-wrote the inbox row with
columns `agent_id` / `owner_user_id` / `updated_at` and no `message_id`.
The real `inbox_table` schema has columns `user_id` / no agent_id /
no updated_at, and `message_id` is NOT NULL UNIQUE. Code/schema drift.

Fix: route through `InboxRepository.create_message`, the canonical
writer, with a generated `bus_*` message_id and a new
`InboxMessageType.MESSAGE_BUS` enum value.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.message_bus.schemas import BusMessage
from xyz_agent_context.schema.inbox_schema import InboxMessageType


@pytest.mark.asyncio
async def test_message_bus_enum_value_is_defined():
    assert InboxMessageType.MESSAGE_BUS.value == "message_bus"


@pytest.mark.asyncio
async def test_write_to_inbox_persists_row_with_correct_schema(
    db_client, monkeypatch
):
    """End-to-end: insert an agent, call _write_to_inbox, assert a
    row appears in inbox_table with the documented schema (user_id,
    message_id, message_type, source) and NO bogus columns."""
    # Patch get_db_client to return our in-memory test client.
    from xyz_agent_context.message_bus import message_bus_trigger as bus_mod

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        bus_mod, "get_db_client", _async_db, raising=False
    )
    # Also patch the lazy import inside _write_to_inbox.
    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client",
        _async_db,
    )

    # Seed an agent so `db.get_one("agents", ...)` succeeds and
    # `agent_row["created_by"]` resolves to the recipient user_id.
    await db_client.insert("agents", {
        "agent_id": "agent_a",
        "agent_name": "A",
        "created_by": "user_x",
    })

    # Build a trigger instance without standing up the real bus.
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)

    msg = BusMessage(
        message_id="m_in_1",
        channel_id="ch_test",
        from_agent="sender_agent",
        content="please summarise this thread",
    )

    await trigger._write_to_inbox(
        agent_id="agent_a",
        channel_id="ch_test",
        trigger_message=msg,
        agent_response="here is the summary",
    )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["user_id"] == "user_x"
    assert row["message_type"] == InboxMessageType.MESSAGE_BUS.value
    assert row["message_id"].startswith("bus_"), row["message_id"]
    assert row["title"].startswith("Message Bus:")
    assert row["content"] == "here is the summary"
    # is_read defaults False (stored as 0 in SQLite, also 0 in MySQL)
    assert row["is_read"] in (0, False)


@pytest.mark.asyncio
async def test_write_to_inbox_swallows_missing_agent(db_client, monkeypatch):
    """If the agent is unknown, log a warning and skip — don't raise."""
    from xyz_agent_context.message_bus import message_bus_trigger as bus_mod

    async def _async_db():
        return db_client

    monkeypatch.setattr(bus_mod, "get_db_client", _async_db, raising=False)
    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client",
        _async_db,
    )

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)

    msg = BusMessage(
        message_id="m_in_2",
        channel_id="ch_test",
        from_agent="sender_agent",
        content="hi",
    )

    # Must not raise.
    await trigger._write_to_inbox(
        agent_id="agent_nonexistent",
        channel_id="ch_test",
        trigger_message=msg,
        agent_response="reply",
    )

    rows = await db_client.get("inbox_table", {})
    assert rows == []
