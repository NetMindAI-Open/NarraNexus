"""
@file_name: test_lark_health.py
@author: Bin Liang
@date: 2026-04-21
@description: /healthz payload + audit wiring tests.

The health payload is what a post-incident reviewer asks first:
  - Is the trigger even alive?
  - How many bots subscribed, how many workers active, how deep is the queue?
  - When did WS last connect / disconnect?
  - How many ingress / error / timeout events in the last hour?

We test the *payload builder* (pure function) here — the HTTP layer is
a trivial FastAPI mount tested end-to-end with httpx.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger
from xyz_agent_context.module.lark_module._health_server import (
    build_health_payload,
)
from xyz_agent_context.repository.lark_trigger_audit_repository import (
    LarkTriggerAuditRepository,
    EVENT_INGRESS_PROCESSED,
    EVENT_WORKER_ERROR,
)


@pytest.mark.asyncio
async def test_health_payload_reports_core_counters(db_client):
    t = LarkTrigger()
    t.running = True
    t._audit_repo = LarkTriggerAuditRepository(db_client)
    t._startup_time_ms = int(time.time() * 1000) - 120_000  # 2 min ago
    t._last_ws_connected_wallclock_ms = int(time.time() * 1000) - 30_000

    # Simulate three subscribers and five workers
    for i in range(3):
        t._subscriber_tasks[f"cli_{i}"] = asyncio.ensure_future(asyncio.sleep(10))
    for i in range(5):
        t._workers.append(asyncio.ensure_future(asyncio.sleep(10)))

    # Some activity in the audit
    await t._audit_repo.append(EVENT_INGRESS_PROCESSED, message_id="om_a")
    await t._audit_repo.append(EVENT_INGRESS_PROCESSED, message_id="om_b")
    await t._audit_repo.append(EVENT_WORKER_ERROR, message_id="om_c", details={"e": "x"})

    payload = await build_health_payload(t)

    assert payload["status"] == "ok"
    assert payload["subscriber_count"] == 3
    assert payload["worker_count"] == 5
    assert payload["queue_depth"] == 0
    assert payload["uptime_seconds"] >= 100  # approximately 120
    assert payload["last_ws_connected_ms"] == t._last_ws_connected_wallclock_ms
    # Counters over the last hour — whichever bucketing we picked, ingress
    # and worker_error must both be represented.
    assert payload["recent_event_counts"][EVENT_INGRESS_PROCESSED] == 2
    assert payload["recent_event_counts"][EVENT_WORKER_ERROR] == 1

    # Cleanup
    for tsk in list(t._subscriber_tasks.values()) + list(t._workers):
        tsk.cancel()
    for tsk in list(t._subscriber_tasks.values()) + list(t._workers):
        try:
            await tsk
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_health_payload_degraded_without_audit(db_client):
    """Degraded state: no audit repo wired (early startup, or fully
    disabled). The payload must still render without crashing."""
    t = LarkTrigger()
    # _audit_repo stays None

    payload = await build_health_payload(t)
    assert payload["status"] == "starting"
    assert payload["recent_event_counts"] == {}
