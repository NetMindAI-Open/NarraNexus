"""
@file_name: test_executor_reaper.py
@date: 2026-06-17
@description: Idle-cull reaper — stops idle executors, skips failures,
no-op without a broker. Pure coordinator, tested via DI fakes.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime.executor_reaper import (
    ExecutorReaper,
    maybe_start_executor_reaper,
)


class _FakeController:
    """Returns a fixed idle set once, then empty (mimics claim semantics)."""

    def __init__(self, idle):
        self._idle = list(idle)

    async def claim_idle_users(self, ttl_seconds):
        users, self._idle = self._idle, []
        return users


@pytest.mark.asyncio
async def test_reap_once_stops_all_idle_users():
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    reaper = ExecutorReaper(_FakeController(["a", "b"]), stop_fn, ttl_seconds=1)
    reaped = await reaper.reap_once()
    assert reaped == ["a", "b"]
    assert stopped == ["a", "b"]


@pytest.mark.asyncio
async def test_reap_once_skips_stop_failures():
    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")

    reaper = ExecutorReaper(_FakeController(["a", "b", "c"]), stop_fn, ttl_seconds=1)
    reaped = await reaper.reap_once()
    assert reaped == ["a", "c"]   # b failed → skipped, pass not aborted


@pytest.mark.asyncio
async def test_reap_once_empty_when_nothing_idle():
    reaper = ExecutorReaper(_FakeController([]), lambda u: None, ttl_seconds=1)
    assert await reaper.reap_once() == []


def test_maybe_start_is_noop_without_broker(monkeypatch):
    monkeypatch.delenv("BROKER_URL", raising=False)
    assert maybe_start_executor_reaper() is None
