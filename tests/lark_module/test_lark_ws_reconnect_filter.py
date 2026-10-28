"""
@file_name: test_lark_ws_reconnect_filter.py
@author: Bin Liang
@date: 2026-04-21
@description: H-5 — historic filter baseline uses max(startup, last-ws-reconnect).

Scenario that motivated this fix:
  1. Process starts at T0.
  2. Runs 3 h healthy.
  3. WebSocket disconnects for 40 min (network partition / Lark blip).
  4. WS reconnects at T_reconnect.
  5. Lark's server-side backlog is released — events created during the
     40 min dark window are pushed at T_reconnect.

With the old baseline (_startup_time_ms only), those backlogged events
have create_time > startup, so Layer 1 passed them. Layer 2 memory TTL
was only 10 min → expired. Only Layer 3 DB kept them out — and if DB
blipped, they got processed.

With the new baseline (max(startup, last_ws_reconnect)), events older
than T_reconnect - 5 min are dropped at Layer 1, which is exactly the
right place: we already missed them live, we shouldn't process them
stale either.
"""
from __future__ import annotations

import time

import pytest

from narranexus_plugins.lark_module.lark_trigger import LarkTrigger
from narranexus.platform.repository.lark_seen_message_repository import (
    LarkSeenMessageRepository,
)


@pytest.mark.asyncio
async def test_event_older_than_last_reconnect_is_dropped(db_client):
    """An event whose create_time predates our last WS reconnect by more
    than HISTORY_BUFFER_MS is a replay, even if it's newer than process
    startup."""
    t = LarkTrigger()
    t._seen_repo = LarkSeenMessageRepository(db_client)
    # Startup was 3 hours ago — baseline moved on since
    t._startup_time_ms = int(time.time() * 1000) - 3 * 3600 * 1000
    # Most recent WS reconnect was 1 minute ago
    t._last_ws_connected_wallclock_ms = int(time.time() * 1000) - 60 * 1000

    # Event from 40 minutes ago — newer than startup, older than reconnect
    stale_ms = t._last_ws_connected_wallclock_ms - 40 * 60 * 1000
    event = {"message_id": "om_backlog", "create_time": str(stale_ms)}

    assert await t._should_process_event(event) is False


@pytest.mark.asyncio
async def test_event_after_last_reconnect_still_processed(db_client):
    """Fresh traffic after the most recent reconnect must go through
    normally — the baseline only shifts the historic cutoff, it does
    not block forward traffic."""
    t = LarkTrigger()
    t._seen_repo = LarkSeenMessageRepository(db_client)
    t._startup_time_ms = int(time.time() * 1000) - 3 * 3600 * 1000
    t._last_ws_connected_wallclock_ms = int(time.time() * 1000) - 60 * 1000

    fresh_ms = t._last_ws_connected_wallclock_ms + 10_000  # 10 s after reconnect
    event = {"message_id": "om_fresh", "create_time": str(fresh_ms)}

    assert await t._should_process_event(event) is True


@pytest.mark.asyncio
async def test_baseline_falls_back_to_startup_when_never_reconnected(db_client):
    """Before the first WS connects (or in tests that don't set it),
    baseline falls back to startup_time, preserving the original
    behaviour."""
    t = LarkTrigger()
    t._seen_repo = LarkSeenMessageRepository(db_client)
    t._startup_time_ms = int(time.time() * 1000)
    # _last_ws_connected_wallclock_ms stays 0

    old_ms = t._startup_time_ms - t.HISTORY_BUFFER_MS - 60_000
    event = {"message_id": "om_historic", "create_time": str(old_ms)}
    assert await t._should_process_event(event) is False
