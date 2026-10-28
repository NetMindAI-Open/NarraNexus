"""
@file_name: test_cancel_watcher.py
@author:
@date: 2026-08-07
@description: CancelWatcher — the cross-process half of "stop this run".

Coverage targets:
  * a pending stop fires the token; no request leaves it alone
  * a stop older than the run does NOT fire (a fresh run must not inherit
    the previous one's flag)
  * a fired token is unregistered — one run is cancelled once
  * a DB failure never propagates to the watched run (binding rule #14:
    the platform is not the interruption source)
  * the poll task starts on first register and stops when nothing is watched
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from narranexus.platform.agent_runtime.cancel_watcher import CancelWatcher
from narranexus.platform.agent_runtime.cancellation import CancellationToken
from narranexus.platform.utils.timezone import utc_now


async def _seed_run(db, event_id: str, *, started_at, cancel_requested_at=None):
    row = {
        "event_id": event_id,
        "trigger": "message_bus",
        "trigger_source": "test",
        "agent_id": "agent_test",
        "user_id": "u_test",
        "state": "running",
        "started_at": started_at,
        "last_event_at": started_at,
        "created_at": "2026-08-07T00:00:00",
        "updated_at": "2026-08-07T00:00:00",
    }
    if cancel_requested_at is not None:
        row["cancel_requested_at"] = cancel_requested_at
    await db.insert("events", row)


@pytest.mark.asyncio
async def test_pending_stop_fires_the_token(db_client):
    started = utc_now() - timedelta(seconds=30)
    await _seed_run(db_client, "evt_stop", started_at=started, cancel_requested_at=utc_now())
    token = CancellationToken()

    watcher = CancelWatcher(db_client, poll_interval_s=0.01)
    watcher.register("evt_stop", token)
    await watcher.poll_once()

    assert token.is_cancelled
    assert "stop" in token.reason.lower()


@pytest.mark.asyncio
async def test_no_request_leaves_the_run_alone(db_client):
    await _seed_run(db_client, "evt_quiet", started_at=utc_now())
    token = CancellationToken()

    watcher = CancelWatcher(db_client, poll_interval_s=0.01)
    watcher.register("evt_quiet", token)
    await watcher.poll_once()

    assert not token.is_cancelled


@pytest.mark.asyncio
async def test_stop_older_than_the_run_does_not_fire(db_client):
    """A stale flag must not kill a run that started after it was raised.

    The flag lives on the events row, and a row can outlive the request that
    touched it (retry, a reused id, a request that landed while the previous
    run was already finishing). Comparing against started_at is what keeps
    "stop" scoped to the run the owner was actually looking at.
    """
    requested = utc_now() - timedelta(seconds=60)
    await _seed_run(db_client, "evt_later", started_at=utc_now(), cancel_requested_at=requested)
    token = CancellationToken()

    watcher = CancelWatcher(db_client, poll_interval_s=0.01)
    watcher.register("evt_later", token)
    await watcher.poll_once()

    assert not token.is_cancelled


@pytest.mark.asyncio
async def test_fired_token_is_unregistered(db_client):
    started = utc_now() - timedelta(seconds=30)
    await _seed_run(db_client, "evt_once", started_at=started, cancel_requested_at=utc_now())
    token = CancellationToken()

    watcher = CancelWatcher(db_client, poll_interval_s=0.01)
    watcher.register("evt_once", token)
    await watcher.poll_once()
    assert token.is_cancelled
    assert not watcher.watching

    # A second pass has nothing to look up — no query, no re-cancel.
    await watcher.poll_once()
    assert not watcher.watching


@pytest.mark.asyncio
async def test_db_failure_never_reaches_the_run(db_client):
    """A watcher that cannot read the DB must degrade to "no stop pending".

    Raising here would surface inside the trigger's task and kill a run that
    is working fine — exactly the failure mode binding rule #14 forbids.
    """

    class _Boom:
        async def execute(self, *a, **kw):
            raise RuntimeError("db is down")

    token = CancellationToken()
    watcher = CancelWatcher(_Boom(), poll_interval_s=0.01)
    watcher.register("evt_any", token)

    await watcher.poll_once()  # must not raise

    assert not token.is_cancelled
    assert watcher.watching  # still watched — the answer was unknown, not "no"


@pytest.mark.asyncio
async def test_poll_task_lifecycle(db_client):
    """The loop exists only while something is watched — an idle process must
    not keep a 1s query running forever."""
    watcher = CancelWatcher(db_client, poll_interval_s=0.01)
    assert not watcher.running

    token = CancellationToken()
    watcher.register("evt_life", token)
    assert watcher.running

    watcher.unregister("evt_life")
    # The loop notices an empty registry and retires itself.
    for _ in range(50):
        if not watcher.running:
            break
        await asyncio.sleep(0.01)
    assert not watcher.running
