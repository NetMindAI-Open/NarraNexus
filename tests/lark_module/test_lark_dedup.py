"""
@file_name: test_lark_dedup.py
@author: Bin Liang
@date: 2026-04-20
@description: LarkTrigger duplicate-event defence tests (Bug 27).

Lark delivers events at-least-once: when the WebSocket reconnects, or the
server doesn't receive our ack in time, the same `message_id` is pushed
again. Prior to this fix the trigger used an in-memory set with a 60 s
TTL, so process restarts or re-deliveries over 60 s old slipped through
and the agent replied to the same message twice (sometimes an hour
apart — exactly what an operator reported).

These tests pin three defences in place:
  1. `LarkSeenMessageRepository.mark_seen` is idempotent across calls
     and across fresh Repository instances (simulating a restart).
  2. `_should_process_event` drops historic replays via a startup-time
     filter (messages created more than HISTORY_BUFFER_MS before
     startup are rejected as replays).
  3. Memory + DB dedup layers compose: a message seen in memory is
     dropped without a DB call; a message missing from memory but
     recorded in DB (e.g. from a previous process lifetime) is also
     dropped.
"""
from __future__ import annotations

import time
from typing import Optional

import pytest

from narranexus_plugins.lark_module.lark_trigger import LarkTrigger
from narranexus.platform.repository.lark_seen_message_repository import (
    LarkSeenMessageRepository,
)


# -------- Repository layer ----------------------------------------------


@pytest.mark.asyncio
async def test_mark_seen_returns_true_on_first_call_false_on_second(db_client):
    repo = LarkSeenMessageRepository(db_client)
    assert await repo.mark_seen("om_msg_abc") is True
    # Same id again → already recorded → False
    assert await repo.mark_seen("om_msg_abc") is False


@pytest.mark.asyncio
async def test_mark_seen_is_durable_across_repository_instances(db_client):
    """Simulates a process restart: a fresh repo against the same DB
    must still see the message as 'already seen'."""
    repo1 = LarkSeenMessageRepository(db_client)
    assert await repo1.mark_seen("om_msg_restart") is True

    # New repo instance, same DB
    repo2 = LarkSeenMessageRepository(db_client)
    assert await repo2.mark_seen("om_msg_restart") is False


@pytest.mark.asyncio
async def test_cleanup_older_than_removes_old_rows(db_client):
    repo = LarkSeenMessageRepository(db_client)
    await repo.mark_seen("om_old")
    await repo.mark_seen("om_new")

    # Simulate the 'old' row ageing past retention: rewrite its seen_at
    # directly via the raw db client. The column is TEXT on sqlite.
    await db_client.update(
        "lark_seen_messages",
        {"message_id": "om_old"},
        {"seen_at": "2020-01-01 00:00:00.000000"},
    )

    deleted = await repo.cleanup_older_than_days(7)
    # old got wiped, new stays
    assert deleted >= 1
    assert await repo.mark_seen("om_new") is False   # still recorded
    assert await repo.mark_seen("om_old") is True    # re-insertable


# -------- Trigger-layer helpers -----------------------------------------


def _trigger_with_startup_now() -> LarkTrigger:
    """Return a LarkTrigger with startup time pinned to 'now' (ms)."""
    t = LarkTrigger()
    t._startup_time_ms = int(time.time() * 1000)
    return t


@pytest.mark.asyncio
async def test_should_process_event_drops_historic_replay(db_client):
    """Events whose Lark-side create_time predates our startup by more
    than HISTORY_BUFFER_MS are replays, not fresh traffic. Drop them."""
    t = _trigger_with_startup_now()
    t._seen_repo = LarkSeenMessageRepository(db_client)

    very_old_ms = t._startup_time_ms - t.HISTORY_BUFFER_MS - 60_000  # 1 min past the buffer
    event = {"message_id": "om_historic", "create_time": str(very_old_ms)}

    assert await t._should_process_event(event) is False


@pytest.mark.asyncio
async def test_should_process_event_accepts_events_within_buffer(db_client):
    """Events within `HISTORY_BUFFER_MS` of startup are legitimate — a
    message the user sent right before we started. Don't drop."""
    t = _trigger_with_startup_now()
    t._seen_repo = LarkSeenMessageRepository(db_client)

    recent_ms = t._startup_time_ms - (t.HISTORY_BUFFER_MS // 2)
    event = {"message_id": "om_within_buffer", "create_time": str(recent_ms)}

    assert await t._should_process_event(event) is True


@pytest.mark.asyncio
async def test_should_process_event_memory_hit_short_circuits(db_client):
    """Second call with the same message_id in the same process →
    memory cache says 'seen', should drop without consulting the DB."""
    t = _trigger_with_startup_now()
    t._seen_repo = LarkSeenMessageRepository(db_client)

    event = {
        "message_id": "om_memory_dup",
        "create_time": str(t._startup_time_ms + 1),
    }
    assert await t._should_process_event(event) is True   # first time: process
    assert await t._should_process_event(event) is False  # second time: dedup


@pytest.mark.asyncio
async def test_should_process_event_db_hit_after_simulated_restart(db_client):
    """Message persisted to DB in a 'prior process' should be dropped by
    a fresh trigger even though its memory cache is empty."""
    # Prior process: repo inserts
    prior_repo = LarkSeenMessageRepository(db_client)
    await prior_repo.mark_seen("om_survives_restart")

    # Fresh trigger — new memory, same DB
    t = _trigger_with_startup_now()
    t._seen_repo = LarkSeenMessageRepository(db_client)

    event = {
        "message_id": "om_survives_restart",
        "create_time": str(t._startup_time_ms + 1),
    }
    assert await t._should_process_event(event) is False


@pytest.mark.asyncio
async def test_should_process_event_missing_create_time_still_dedups(db_client):
    """A pathological event without create_time must still be deduped by
    id, not silently accepted twice."""
    t = _trigger_with_startup_now()
    t._seen_repo = LarkSeenMessageRepository(db_client)

    event = {"message_id": "om_no_time"}  # no create_time
    assert await t._should_process_event(event) is True
    assert await t._should_process_event(event) is False
