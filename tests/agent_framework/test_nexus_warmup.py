"""
@file_name: test_nexus_warmup.py
@date: 2026-08-20
@description: NexusAgent.warmup() eagerly fills the warm-runner pool at executor
startup so the process's FIRST nexus_power turn also gets a pre-imported runner
(measured ~12s cold vs ~2s warm on dev). Shared gate with __init__ via
_schedule_pool_prewarm(): no-op in in-process mode or when pooling is disabled,
and — the contract that matters — never raised into the caller, including when
there is no running event loop (schedule_refill's create_task would otherwise
raise).
"""
import asyncio

import pytest

from narranexus_plugins.frameworks_nexus_power.adapter import nexus_agent as na


def _fake_pool(enabled, calls):
    class FakePool:
        pass

    FakePool.enabled = enabled
    FakePool.schedule_refill = lambda self: calls.__setitem__("refill", calls["refill"] + 1)
    return FakePool()


@pytest.mark.asyncio
async def test_warmup_schedules_pool_refill_in_subprocess_mode(monkeypatch):
    monkeypatch.delenv("NEXUS_POWER_INPROCESS", raising=False)
    calls = {"refill": 0}
    monkeypatch.setattr(na._WarmRunnerPool, "shared", classmethod(lambda cls: _fake_pool(True, calls)))
    agent = na.NexusAgent()
    calls["refill"] = 0  # isolate warmup's own call from __init__'s shared prewarm
    agent.warmup()
    assert calls["refill"] == 1


@pytest.mark.asyncio
async def test_warmup_is_noop_in_inprocess_mode(monkeypatch):
    monkeypatch.setenv("NEXUS_POWER_INPROCESS", "1")
    calls = {"refill": 0}
    monkeypatch.setattr(na._WarmRunnerPool, "shared", classmethod(lambda cls: _fake_pool(True, calls)))
    agent = na.NexusAgent()
    calls["refill"] = 0  # isolate warmup's own call from __init__'s shared prewarm
    agent.warmup()
    assert calls["refill"] == 0


@pytest.mark.asyncio
async def test_warmup_is_noop_when_pool_disabled(monkeypatch):
    monkeypatch.delenv("NEXUS_POWER_INPROCESS", raising=False)
    calls = {"refill": 0}
    monkeypatch.setattr(na._WarmRunnerPool, "shared", classmethod(lambda cls: _fake_pool(False, calls)))
    agent = na.NexusAgent()
    calls["refill"] = 0  # isolate warmup's own call from __init__'s shared prewarm
    agent.warmup()
    assert calls["refill"] == 0


def test_warmup_does_not_raise_without_running_loop(monkeypatch):
    # Real pool + subprocess mode + size>0, called synchronously (no running
    # loop). schedule_refill -> create_task would raise "no running event loop";
    # the best-effort contract says warmup must swallow that and not raise.
    monkeypatch.delenv("NEXUS_POWER_INPROCESS", raising=False)
    monkeypatch.setenv("NEXUS_POWER_POOL_SIZE", "1")
    na._WarmRunnerPool._shared = None
    try:
        agent = na.NexusAgent()
        agent.warmup()  # must NOT raise
    finally:
        na._WarmRunnerPool._shared = None


@pytest.mark.asyncio
async def test_warmup_fills_pool_up_to_size_with_running_loop(monkeypatch):
    # Real pool with a running loop: warmup fills _idle up to size (not beyond),
    # exercising the real schedule_refill -> _refill -> spawn path. spawn is
    # stubbed so no real runner subprocess is started.
    monkeypatch.delenv("NEXUS_POWER_INPROCESS", raising=False)
    monkeypatch.setenv("NEXUS_POWER_POOL_SIZE", "1")
    na._WarmRunnerPool._shared = None
    spawned = {"n": 0}

    class _FakeProc:
        returncode = None

    async def fake_spawn(self, *, prewarm):
        spawned["n"] += 1
        return _FakeProc()

    monkeypatch.setattr(na._WarmRunnerPool, "spawn", fake_spawn)
    pool = na._WarmRunnerPool.shared()
    try:
        agent = na.NexusAgent()  # __init__ (has a loop here) already schedules a refill
        agent.warmup()           # idempotent second schedule
        # Bounded poll instead of a fixed sleep: deterministic and not dependent
        # on the fire-and-forget refill task winning a scheduling slot inside a
        # fixed wall-clock window (flaky on preempted CI runners).
        for _ in range(100):
            if len(pool._idle) >= 1:
                break
            await asyncio.sleep(0.01)
        assert len(pool._idle) == 1   # filled to size, not exceeded
        assert spawned["n"] == 1      # 2nd refill saw idle>=size and did not spawn
    finally:
        # Drop the fake procs BEFORE atexit's _shutdown_sync (which reads
        # process.pid) can see them — even if an assertion above failed.
        pool._idle.clear()
        na._WarmRunnerPool._shared = None
