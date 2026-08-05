"""
@file_name: test_bus_hop_timing.py
@date: 2026-08-05
@description: [bus-timing] — the per-hop measurement line.

The 2026-08-01 event measured "a bus hop = 45-95s" with no way to split
"sat in the queue" from "the turn itself" (Base recvrdLPavdQgU). Every
successful ``_handle_channel_batch`` now emits one grep-stable line:

    [bus-timing] agent=.. channel=.. team=.. batch=N
                 queue_wait_s=.. turn_s=.. hop_s=..

Pinned here:
* the line fires on the team branch and the DM branch,
* queue_wait_s parses a real created_at (datetime or ISO string — SQLite
  vs MySQL) and falls back to -1.0 rather than crashing when absent,
* a failed turn emits no timing line (the failure path has its own story).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from loguru import logger

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
)
from xyz_agent_context.message_bus.schemas import BusMessage

ROOM = "ch_timing_room"

_TIMING_RE = re.compile(
    r"\[bus-timing\] agent=(?P<agent>\S+) channel=(?P<channel>\S+) "
    r"team=(?P<team>\S+) batch=(?P<batch>\d+) "
    r"queue_wait_s=(?P<queue>-?\d+\.\d+) oldest_wait_s=(?P<oldest>-?\d+\.\d+) "
    r"turn_s=(?P<turn>\d+\.\d+) hop_s=(?P<hop>-?\d+\.\d+)"
)


@pytest.fixture
def log_lines():
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


def _timing_hits(lines: list[str]) -> list[re.Match]:
    return [m for ln in lines if (m := _TIMING_RE.search(ln))]


def _patch_db_factory(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _async_db
    )


async def _trigger_with_fake_runtime(db_client, monkeypatch, *, fail=False):
    _patch_db_factory(monkeypatch, db_client)
    await db_client.insert(
        "agents", {"agent_id": "agent_a", "agent_name": "A", "created_by": "user_x"}
    )
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    if fail:
        async def _fake_invoke(*_a, **_k):
            raise RuntimeError("turn blew up")
    else:
        async def _fake_invoke(*_a, **_k):
            return "the reply", "evt_t1"

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake_invoke)
    return trigger


@pytest.mark.asyncio
async def test_team_hop_emits_timing_line(db_client, monkeypatch, log_lines):
    trigger = await _trigger_with_fake_runtime(db_client, monkeypatch)
    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x",
        content="@A hello",
        created_at=datetime.now(timezone.utc),
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    hits = _timing_hits(log_lines)
    assert len(hits) == 1
    m = hits[0]
    assert m["agent"] == "agent_a" and m["team"] == "True" and m["batch"] == "1"
    # created_at was "now" → queue wait is real and tiny, never the -1 fallback.
    assert 0.0 <= float(m["queue"]) < 30.0
    # Single-message batch: the oldest wait IS the trigger's wait.
    assert float(m["oldest"]) >= float(m["queue"]) >= 0.0
    # hop covers queue + turn + delivery.
    assert float(m["hop"]) >= float(m["turn"])


@pytest.mark.asyncio
async def test_dm_hop_with_iso_string_created_at(db_client, monkeypatch, log_lines):
    """SQLite hands created_at back as an ISO string — must parse, not crash."""
    trigger = await _trigger_with_fake_runtime(db_client, monkeypatch)
    msg = BusMessage(
        message_id="m2", channel_id=ROOM, from_agent="agent_b",
        content="question for you",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await trigger._handle_channel_batch("agent_a", ROOM, [msg], msg)

    hits = _timing_hits(log_lines)
    assert len(hits) == 1
    assert hits[0]["team"] == "False"
    assert 0.0 <= float(hits[0]["queue"]) < 30.0


@pytest.mark.asyncio
async def test_missing_created_at_falls_back_not_crashes(db_client, monkeypatch, log_lines):
    trigger = await _trigger_with_fake_runtime(db_client, monkeypatch)
    msg = BusMessage(
        message_id="m3", channel_id=ROOM, from_agent="agent_b",
        content="no timestamp",
    )
    await trigger._handle_channel_batch("agent_a", ROOM, [msg], msg)

    hits = _timing_hits(log_lines)
    assert len(hits) == 1
    assert float(hits[0]["queue"]) == -1.0
    # hop follows the same convention — a p50/p99 aggregation can drop the
    # incomplete rows on one filter instead of silently mixing in numbers
    # that quietly changed definition (insert->delivered vs dispatch->delivered).
    assert float(hits[0]["hop"]) == -1.0
    # turn is still real (it never depends on created_at).
    assert float(hits[0]["turn"]) >= 0.0


@pytest.mark.asyncio
async def test_failed_turn_emits_no_timing_line(db_client, monkeypatch, log_lines):
    trigger = await _trigger_with_fake_runtime(db_client, monkeypatch, fail=True)
    msg = BusMessage(
        message_id="m4", channel_id=ROOM, from_agent="usr_user_x",
        content="@A hello",
        created_at=datetime.now(timezone.utc),
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )
    assert _timing_hits(log_lines) == []
