"""
@file_name: test_channel_info_dialect.py
@author: Bin Liang
@date: 2026-06-09
@description: MessageBusTrigger._get_channel_info must resolve channels on SQLite.

Regression (2026-06-09): the query used a MySQL ``%s`` placeholder that SQLite
rejects with ``near "%": syntax error``. The raw ``backend.execute`` path does
NOT translate dialects, so the lookup threw on every poll cycle for any agent
that had channel messages — silently breaking bus delivery. User-visible
symptom: agents 影/镜 never received the bus messages 零(Loki) sent them, so
they stayed silent. Fix routes through the dialect-aware ``db.get_one``.
"""
import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger


@pytest.mark.asyncio
async def test_get_channel_info_resolves_on_sqlite(db_client):
    await db_client.insert("bus_channels", {
        "channel_id": "ch_x",
        "name": "direct:agent_zero:agent_shadow",
        "channel_type": "direct",
        "created_by": "agent_zero",
    })
    # Production passes the RAW backend (db._backend) to LocalMessageBus, which
    # bypasses AsyncDatabaseClient's %s→? dialect translation — that is exactly
    # what exposed the bug. The test must mirror that, not the wrapper.
    trigger = MessageBusTrigger(LocalMessageBus(db_client._backend))

    channel_type, owner = await trigger._get_channel_info("ch_x")

    assert channel_type == "direct"
    assert owner == "agent_zero"


@pytest.mark.asyncio
async def test_get_channel_info_missing_channel_defaults(db_client):
    # Production passes the RAW backend (db._backend) to LocalMessageBus, which
    # bypasses AsyncDatabaseClient's %s→? dialect translation — that is exactly
    # what exposed the bug. The test must mirror that, not the wrapper.
    trigger = MessageBusTrigger(LocalMessageBus(db_client._backend))

    result = await trigger._get_channel_info("ch_absent")

    assert result == ("group", "")
