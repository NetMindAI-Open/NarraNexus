"""
@file_name: test_lark_worker_self_heal.py
@author: Bin Liang
@date: 2026-04-21
@description: H-4 — dead workers are pruned so _adjust_workers can rebuild them.

Before: `_adjust_workers` only compared `len(self._workers)` against
target; if a worker task ended (uncaught exception, external cancel)
the list still counted it as alive and no fresh worker was scheduled.
The queue then grew unbounded with no consumer.

After: `_prune_dead_workers` drops any `.done()` task; the watcher
loop calls it each cycle before `_adjust_workers`, which then fills
the pool back up.
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger


@pytest.mark.asyncio
async def test_prune_drops_done_tasks():
    t = LarkTrigger()

    async def _succeed():
        return

    async def _never_returns():
        while True:
            await asyncio.sleep(1)

    done = asyncio.ensure_future(_succeed())
    alive = asyncio.ensure_future(_never_returns())
    await asyncio.sleep(0)  # let _succeed complete
    await done

    t._workers = [done, alive]
    pruned = t._prune_dead_workers()

    assert pruned == 1
    assert t._workers == [alive]

    alive.cancel()
    try:
        await alive
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_adjust_workers_refills_after_prune():
    """End-to-end of the self-heal: mark a worker as done, prune,
    _adjust_workers should schedule a fresh replacement."""
    t = LarkTrigger()
    t.running = True  # so the freshly-spawned _worker doesn't exit immediately

    async def _succeed():
        return

    done = asyncio.ensure_future(_succeed())
    await done

    t._workers = [done]
    t._prune_dead_workers()
    assert t._workers == []

    t._adjust_workers(target=2)
    assert len(t._workers) == 2
    # Tear down
    t.running = False
    for w in t._workers:
        w.cancel()
    for w in t._workers:
        try:
            await w
        except asyncio.CancelledError:
            pass
