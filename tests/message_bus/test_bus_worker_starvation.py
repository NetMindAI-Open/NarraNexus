"""
@file_name: test_bus_worker_starvation.py
@date: 2026-08-14
@description: The worker pool running dry becomes visible instead of implicit.

`liveness_snapshot()` has carried `running` / `waiting` / `max_workers` since
the 2026-07-27 wedge, and its docstring already names the signal: sustained
`running == max_workers` with `waiting > 0` means the POOL is the bottleneck,
not the agents. Nobody reads it. It goes into a heartbeat row and stops there.

That matters for PRD "Team chat responsiveness" specifically, because slot wait is
inside `queue_wait_ms` — the column acceptance #1 is judged on. A starved pool
shows up as "the bus got slower" with no way to tell it from "the agents got
slower".

The threshold is WALL CLOCK, not a cycle count, and these tests drive a fake
clock to say so. The first implementation counted cycles and looked correct
under test — because a test loop makes cycles instantaneous. On a live instance
it silently missed a real 28-second starvation: `_poll_cycle` returns zero
dispatches while candidates queue behind the semaphore, so the adaptive
interval backs off 3 -> 6 -> 9 -> 12s, and 28 seconds of shortage produced only
FOUR cycles against a five-cycle threshold. Poll cycles get rarer exactly when
starvation is happening. Hence `test_cycle_frequency_does_not_change_the_verdict`.

What is pinned here:

* an alert fires only after the condition HOLDS for `STARVATION_ALERT_AFTER_S`
  — one busy moment is a working pool, not an incident,
* how OFTEN the check runs cannot change the verdict,
* a single free slot resets the episode,
* the alert repeats at most once per episode, so a pool that stays saturated
  for an hour writes one row and not sixty,
* it is DIAGNOSTIC ONLY: nothing is cancelled, no turn is force-stopped, and
  the alert never reaches the owner's inbox (binding rule #14 — a long run is a
  legitimate workload, and a slot shortage is a platform problem the owner
  cannot fix anyway).
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus import message_bus_trigger as mbt
from narranexus.platform.message_bus.message_bus_trigger import (
    STARVATION_ALERT_AFTER_S,
    MessageBusTrigger,
    _InFlight,
)


class _Clock:
    """A monotonic clock the test advances by hand.

    Load-bearing: the whole defect this file now guards against was invisible
    to a test that let real time stand still while cycles flew by.
    """

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()

    class _TimeShim:
        monotonic = staticmethod(c)

        def __getattr__(self, name):
            import time as _real
            return getattr(_real, name)

    monkeypatch.setattr(mbt, "time", _TimeShim())
    return c


class _FakeAudit:
    def __init__(self):
        self.errors: list = []

    async def error(self, detail=None):
        self.errors.append(detail)

    async def heartbeat(self, detail=None):
        return None

    async def started(self, detail=None):
        return None

    async def stopped(self, detail=None):
        return None


def _trigger(max_workers: int = 2) -> MessageBusTrigger:
    t = MessageBusTrigger(bus=object(), max_workers=max_workers)
    t.audit = _FakeAudit()  # type: ignore[assignment]
    return t


def _saturate(t: MessageBusTrigger, *, running: int, waiting: int) -> None:
    """Populate `_in_flight` so liveness_snapshot reports the wanted shape."""
    t._in_flight.clear()
    for i in range(running):
        f = _InFlight(task=None, started_at=0.0)  # type: ignore[arg-type]
        f.running = True
        t._in_flight[(f"run_{i}", "ch")] = f  # keys are (agent, channel) lanes
    for i in range(waiting):
        f = _InFlight(task=None, started_at=0.0)  # type: ignore[arg-type]
        f.running = False
        t._in_flight[(f"wait_{i}", "ch")] = f


@pytest.mark.asyncio
async def test_one_saturated_moment_is_not_an_incident(clock):
    """A full pool with someone queued is normal under load."""
    t = _trigger(max_workers=2)
    _saturate(t, running=2, waiting=1)

    await t._check_worker_starvation()
    clock.advance(STARVATION_ALERT_AFTER_S / 4)
    await t._check_worker_starvation()

    assert t.audit.errors == []


@pytest.mark.asyncio
async def test_a_sustained_shortage_alerts_once(clock):
    t = _trigger(max_workers=2)
    _saturate(t, running=2, waiting=3)

    await t._check_worker_starvation()
    clock.advance(STARVATION_ALERT_AFTER_S + 1)
    await t._check_worker_starvation()

    assert len(t.audit.errors) == 1
    detail = t.audit.errors[0]
    assert detail["stage"] == "worker_starvation"
    assert detail["waiting"] == 3
    assert detail["max_workers"] == 2
    assert detail["starved_for_s"] >= int(STARVATION_ALERT_AFTER_S)
    # The point of the alert is to name WHICH lane to look at (agent@channel),
    # and whether one agent is hogging the pool or many each hold a slot.
    assert detail["longest_running_lane"] == "run_0@ch"
    assert detail["distinct_agents"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("checks", [2, 5, 40])
async def test_cycle_frequency_does_not_change_the_verdict(clock, checks):
    """The defect this file exists to prevent, stated directly.

    The same 30 seconds of shortage must alert whether the loop sampled it
    twice or forty times. A cycle-counting threshold fails the `checks=2`
    case — which is the real one, because the adaptive poll backs off to 12s
    precisely while nothing can be dispatched.
    """
    t = _trigger(max_workers=2)
    _saturate(t, running=2, waiting=1)

    step = 30.0 / checks
    for _ in range(checks):
        await t._check_worker_starvation()
        clock.advance(step)
    await t._check_worker_starvation()

    assert len(t.audit.errors) == 1


@pytest.mark.asyncio
async def test_a_saturated_pool_does_not_alert_forever(clock):
    """An hour of the same condition is one problem, not one per check."""
    t = _trigger(max_workers=2)
    _saturate(t, running=2, waiting=1)

    for _ in range(60):
        await t._check_worker_starvation()
        clock.advance(STARVATION_ALERT_AFTER_S)

    assert len(t.audit.errors) == 1, (
        "a persistent shortage re-alerted — that is how an alarm becomes "
        "noise nobody reads (lesson #3)"
    )


@pytest.mark.asyncio
async def test_a_free_slot_resets_the_episode(clock):
    t = _trigger(max_workers=2)

    _saturate(t, running=2, waiting=1)
    await t._check_worker_starvation()
    clock.advance(STARVATION_ALERT_AFTER_S * 0.9)

    # One check with a free slot.
    _saturate(t, running=1, waiting=0)
    await t._check_worker_starvation()
    clock.advance(1)

    # Back to saturated, but the clock restarted — not yet an alert.
    _saturate(t, running=2, waiting=1)
    await t._check_worker_starvation()
    clock.advance(STARVATION_ALERT_AFTER_S * 0.9)
    await t._check_worker_starvation()

    assert t.audit.errors == []


@pytest.mark.asyncio
async def test_a_full_pool_with_nobody_waiting_is_not_starvation(clock):
    """`running == max_workers` alone just means the pool is being used."""
    t = _trigger(max_workers=2)
    _saturate(t, running=2, waiting=0)

    for _ in range(10):
        await t._check_worker_starvation()
        clock.advance(STARVATION_ALERT_AFTER_S)

    assert t.audit.errors == []


@pytest.mark.asyncio
async def test_the_alert_never_force_stops_anything(clock):
    """Binding rule #14: diagnostic only. No cancellation, ever."""
    t = _trigger(max_workers=2)
    _saturate(t, running=2, waiting=2)
    before = dict(t._in_flight)

    for _ in range(5):
        await t._check_worker_starvation()
        clock.advance(STARVATION_ALERT_AFTER_S)

    assert t._in_flight == before, "starvation handling touched in-flight turns"


def test_max_workers_is_configurable_and_defaults_sanely():
    """Hard-coded 3 was the whole reason a slot shortage was invisible AND
    unfixable without a code change. The pool size is OUR resource decision,
    not a limit on agents — raising it is what "do not become the interruption
    source" asks for."""
    from narranexus.platform.settings import settings

    assert isinstance(settings.bus_max_workers, int)
    assert settings.bus_max_workers >= 1

    t = MessageBusTrigger(bus=object())
    assert t._max_workers == settings.bus_max_workers
