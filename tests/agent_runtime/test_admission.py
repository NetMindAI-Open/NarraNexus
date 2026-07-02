"""
@file_name: test_admission.py
@date: 2026-06-17
@description: Two-level admission gate — global + per-user caps, memory
guard, queue-then-wake, and the local "unlimited" no-op (rule #7).

Locks the anti-OOM behaviour: a single user driving many agents (chat +
jobs + bus) can never exceed its caps; new runs queue (never interrupt,
rule #14) and proceed when a slot frees.
"""
from __future__ import annotations

import asyncio

import pytest

import xyz_agent_context.agent_runtime.admission as adm
from xyz_agent_context.agent_runtime.admission import AgentAdmissionController


async def _blocks(coro_factory) -> asyncio.Task:
    """Start an acquire; assert it's still waiting after a tick."""
    t = asyncio.create_task(coro_factory())
    await asyncio.sleep(0.05)
    assert not t.done(), "expected acquire to queue (be blocked)"
    return t


@pytest.mark.asyncio
async def test_per_user_cap_queues_then_wakes():
    c = AgentAdmissionController(max_users=None, max_loops_per_user=2, max_loops_global=None, min_free_mem_mb=0)
    t1 = await c.acquire("u")
    t2 = await c.acquire("u")
    blocked = await _blocks(lambda: c.acquire("u"))   # 3rd over per-user cap
    await c.release(t1)                                 # frees a per-user slot
    await asyncio.sleep(0.05)
    assert blocked.done()
    await c.release(t2)
    await c.release(await blocked)


@pytest.mark.asyncio
async def test_global_loop_cap():
    c = AgentAdmissionController(max_users=None, max_loops_per_user=None, max_loops_global=2, min_free_mem_mb=0)
    a = await c.acquire("a")
    b = await c.acquire("b")
    blocked = await _blocks(lambda: c.acquire("c"))     # 3rd over global cap
    await c.release(a)
    await asyncio.sleep(0.05)
    assert blocked.done()
    await c.release(b)
    await c.release(await blocked)


@pytest.mark.asyncio
async def test_user_slot_cap():
    c = AgentAdmissionController(max_users=2, max_loops_per_user=None, max_loops_global=None, min_free_mem_mb=0)
    a = await c.acquire("a")
    b = await c.acquire("b")                            # 2 distinct active users
    blocked = await _blocks(lambda: c.acquire("c"))     # 3rd distinct user blocked
    await c.release(a)                                  # user a no longer active
    await asyncio.sleep(0.05)
    assert blocked.done()
    await c.release(b)
    await c.release(await blocked)


@pytest.mark.asyncio
async def test_disabled_never_blocks():
    c = AgentAdmissionController(max_users=None, max_loops_per_user=None, max_loops_global=None, min_free_mem_mb=0)
    assert not c.enabled
    toks = [await asyncio.wait_for(c.acquire("u"), timeout=0.2) for _ in range(20)]
    assert len(toks) == 20
    for t in toks:
        await c.release(t)


@pytest.mark.asyncio
async def test_memory_guard_holds_then_releases(monkeypatch):
    c = AgentAdmissionController(max_users=None, max_loops_per_user=None, max_loops_global=None, min_free_mem_mb=4096)
    monkeypatch.setattr(adm, "_free_mem_mb", lambda: 1000.0)   # below threshold
    blocked = await _blocks(lambda: c.acquire("u"))
    monkeypatch.setattr(adm, "_free_mem_mb", lambda: 9000.0)   # recovered
    async with c._cond:                                        # nudge the waiter to re-check
        c._cond.notify_all()
    await asyncio.sleep(0.05)
    assert blocked.done()
    await c.release(await blocked)


@pytest.mark.asyncio
async def test_slot_context_manager_releases_on_exit():
    c = AgentAdmissionController(max_users=None, max_loops_per_user=1, max_loops_global=None, min_free_mem_mb=0)
    async with c.slot("u"):
        assert c._per_user.get("u") == 1
    assert c._per_user.get("u", 0) == 0   # released on exit


@pytest.mark.asyncio
async def test_idle_tracking_and_claim():
    now = {"t": 1000.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    tok = await c.acquire("u")
    assert await c.claim_idle_users(60) == []      # active → not idle
    await c.release(tok)                            # idle since t=1000
    assert await c.claim_idle_users(60) == []       # 0s elapsed < ttl
    now["t"] = 1070.0
    assert await c.claim_idle_users(60) == ["u"]    # 70s >= ttl
    assert await c.claim_idle_users(60) == []       # consumed (un-tracked)


@pytest.mark.asyncio
async def test_active_user_never_claimed():
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.acquire("u")                            # never released → active
    now["t"] = 99999.0
    assert await c.claim_idle_users(1) == []


@pytest.mark.asyncio
async def test_reacquire_clears_idle():
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.release(await c.acquire("u"))           # idle @ 0
    now["t"] = 100.0
    await c.acquire("u")                            # active again → idle cleared
    assert await c.claim_idle_users(1) == []


def test_cloud_defaults(monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.utils.deployment_mode.get_deployment_mode", lambda: "cloud"
    )
    for k in ("MAX_CONCURRENT_USERS", "MAX_LOOPS_PER_USER", "MAX_CONCURRENT_LOOPS", "MIN_FREE_MEM_MB"):
        monkeypatch.delenv(k, raising=False)
    adm.reset_admission_controller_for_test(None)
    c = adm.get_admission_controller()
    assert (c.max_users, c.max_loops_per_user, c.max_loops_global, c.min_free_mem_mb) == (20, 5, 50, 6144)
    adm.reset_admission_controller_for_test(None)
