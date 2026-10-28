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

import narranexus.platform.agent_runtime.admission as adm
from narranexus.platform.agent_runtime.admission import AgentAdmissionController


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
        "narranexus.platform.utils.deployment_mode.get_deployment_mode", lambda: "cloud"
    )
    for k in ("MAX_CONCURRENT_USERS", "MAX_LOOPS_PER_USER", "MAX_CONCURRENT_LOOPS", "MIN_FREE_MEM_MB"):
        monkeypatch.delenv(k, raising=False)
    adm.reset_admission_controller_for_test(None)
    c = adm.get_admission_controller()
    assert (c.max_users, c.max_loops_per_user, c.max_loops_global, c.min_free_mem_mb) == (20, 5, 50, 6144)
    adm.reset_admission_controller_for_test(None)


# --------------------------------------------------------------------------
# Cross-process veto (feeds the executor reaper) — 2026-07-31 incident
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vetoed_user_is_withheld_but_keeps_its_stamp():
    """The veto must withhold WITHOUT consuming. Filtering in the caller
    instead would drop the stamp, and a user driven mostly from another
    process gets a new one only on ITS next release here — i.e. never."""
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.release(await c.acquire("u"))
    now["t"] = 100.0

    busy = {"u"}

    async def is_busy(user_id):
        return user_id in busy

    assert await c.claim_idle_users(1, is_busy=is_busy) == []
    busy.clear()
    # Still tracked, and its ORIGINAL idle stamp — no free extra TTL.
    assert await c.claim_idle_users(1, is_busy=is_busy) == ["u"]


@pytest.mark.asyncio
async def test_veto_failure_reads_as_busy():
    """No verdict must never authorise a stop (binding rule #14)."""
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.release(await c.acquire("u"))
    now["t"] = 100.0

    async def boom(user_id):
        raise RuntimeError("db down")

    assert await c.claim_idle_users(1, is_busy=boom) == []
    assert await c.claim_idle_users(1) == ["u"]   # stamp survived


@pytest.mark.asyncio
async def test_user_reactivated_during_the_veto_is_not_claimed():
    """The veto runs outside the lock, so acquire/release can land mid-flight.
    The second pass re-checks the stamp is still the one we judged."""
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.release(await c.acquire("u"))
    now["t"] = 100.0

    async def is_busy(user_id):
        await c.acquire(user_id)     # a run starts while we are asking
        return False

    assert await c.claim_idle_users(1, is_busy=is_busy) == []


@pytest.mark.asyncio
async def test_restamp_idle_puts_a_backed_off_user_back_in_the_pool():
    """The claim consumed the stamp; without a restamp this user would only
    be reconsidered on its next release IN THIS PROCESS — never, for a user
    driven from workers. It waits a fresh TTL, which is truthful: it was busy
    a moment ago."""
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.release(await c.acquire("u"))            # idle @ 0
    now["t"] = 100.0
    assert await c.claim_idle_users(1) == ["u"]
    await c.restamp_idle("u")                        # reaper backed off @ 100
    assert await c.claim_idle_users(1) == []         # not due yet
    now["t"] = 200.0
    assert await c.claim_idle_users(1) == ["u"]      # due again


@pytest.mark.asyncio
async def test_restamp_idle_keeps_an_existing_older_stamp():
    """setdefault, not assignment: if a release landed while the reaper was
    backing off, that stamp is the truthful one and must not be pushed back."""
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.release(await c.acquire("u"))            # idle @ 0
    now["t"] = 100.0
    await c.restamp_idle("u")                        # must NOT overwrite
    assert await c.claim_idle_users(50) == ["u"]     # judged against t=0


@pytest.mark.asyncio
async def test_restamp_idle_ignores_an_active_user():
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.acquire("u")
    await c.restamp_idle("u")
    now["t"] = 100.0
    assert await c.claim_idle_users(1) == []


@pytest.mark.asyncio
async def test_veto_budget_exhaustion_withholds_the_unjudged():
    """A wedged DB must stall culling, not the reaper loop: candidates left
    unjudged when the budget runs out read as busy and keep their stamps."""
    now = {"t": 0.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.release(await c.acquire("a"))
    await c.release(await c.acquire("b"))
    now["t"] = 100.0

    async def slow(user_id):
        now["t"] += adm._VETO_BUDGET_S      # the first call burns the budget
        return False

    assert await c.claim_idle_users(1, is_busy=slow) == ["a"]
    assert await c.claim_idle_users(1) == ["b"]   # never judged, never lost
