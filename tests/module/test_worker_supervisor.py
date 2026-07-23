"""
@file_name: test_worker_supervisor.py
@author: NetMind.AI
@date: 2026-07-22
@description: Unit tests for the consolidated worker supervisor.

Guards the core of ``module/run_worker_supervisor.py`` that merged the four
separate worker processes (poller / jobs / bus / channels) into one supervised
event loop:

- ``build_specs`` selection (--only / --exclude / unknown names / empty set)
- ``_supervise`` restart semantics: crash -> audited backoff-restart; unexpected
  normal return -> restart WITHOUT an error audit; ``CancelledError`` -> propagate
  and never restart; backoff sleep interruptible by ``stop_event`` (binding rule
  #14: no run-duration cap, only crash backoff).
- the channels adapter blocks on ``stop_event`` and drains (stops triggers +
  cancels the health task) on shutdown.

All tests use fakes — no real DB, no bound ports.
"""

import asyncio

import pytest

from xyz_agent_context.module import run_worker_supervisor as sup


class FakeAudit:
    """Records lifecycle calls; every method is a no-op coroutine."""

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.errors = 0
        self.beats = 0

    async def started(self, detail=None) -> None:
        self.starts += 1

    async def stopped(self, detail=None) -> None:
        self.stops += 1

    async def error(self, detail=None) -> None:
        self.errors += 1

    async def heartbeat(self, detail=None, force: bool = False) -> None:
        self.beats += 1


def _ctx(**kw) -> sup.SupervisorContext:
    return sup.SupervisorContext(
        db=kw.pop("db", None),
        audit=kw.pop("audit", FakeAudit()),
        stop_event=asyncio.Event(),
        **kw,
    )


# ---------------------------------------------------------------------------
# build_specs
# ---------------------------------------------------------------------------


def test_build_specs_default_is_all_in_canonical_order():
    specs = sup.build_specs()
    assert [s.name for s in specs] == list(sup.ALL_WORKERS)


def test_build_specs_only_subset():
    specs = sup.build_specs(only={"poller", "jobs"})
    # canonical order preserved, subset applied
    assert [s.name for s in specs] == ["poller", "jobs"]


def test_build_specs_exclude():
    specs = sup.build_specs(exclude={"channels"})
    assert "channels" not in {s.name for s in specs}
    assert set(s.name for s in specs) == set(sup.ALL_WORKERS) - {"channels"}


def test_build_specs_unknown_name_warns_but_returns_valid_subset():
    # 'nope' is unknown; 'bus' is valid -> subset is just {bus}, no crash.
    specs = sup.build_specs(only={"bus", "nope"})
    assert [s.name for s in specs] == ["bus"]


def test_build_specs_empty_selection_is_allowed():
    specs = sup.build_specs(only={"nope"})
    assert specs == []


# ---------------------------------------------------------------------------
# _supervise — restart semantics
# ---------------------------------------------------------------------------


async def test_crash_is_audited_and_restarted_with_backoff(monkeypatch):
    monkeypatch.setattr(sup, "_BACKOFF_BASE", 0.001)
    monkeypatch.setattr(sup, "_BACKOFF_CAP", 0.001)
    ctx = _ctx()
    audit: FakeAudit = ctx.audit  # type: ignore[assignment]

    attempts: list[int] = []
    block = asyncio.Event()

    async def factory(_ctx):
        i = len(attempts)
        attempts.append(i)

        async def _run():
            if i < 2:
                raise RuntimeError(f"boom{i}")
            await block.wait()  # 3rd attempt blocks (healthy long run)

        return sup.WorkerHandle(run=_run(), stop=lambda: None)

    spec = sup.WorkerSpec("t", factory, stable_after_s=999)
    task = asyncio.ensure_future(sup._supervise(ctx, spec))

    for _ in range(200):
        if len(attempts) >= 3:
            break
        await asyncio.sleep(0.005)

    assert len(attempts) >= 3, "worker was not restarted after crashes"
    assert ctx.restarts["t"] == 2, "restart counter should count the 2 crashes"
    assert audit.errors == 2, "each crash should emit one audit.error"
    assert ctx._states["t"]["state"] == "running"

    # cleanup
    ctx.stop_event.set()
    block.set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_unexpected_normal_return_restarts_without_error_audit(monkeypatch):
    monkeypatch.setattr(sup, "_BACKOFF_BASE", 0.001)
    ctx = _ctx()
    audit: FakeAudit = ctx.audit  # type: ignore[assignment]

    attempts: list[int] = []
    block = asyncio.Event()

    async def factory(_ctx):
        i = len(attempts)
        attempts.append(i)

        async def _run():
            if i == 0:
                return  # returns immediately while NOT shutting down
            await block.wait()

        return sup.WorkerHandle(run=_run(), stop=lambda: None)

    spec = sup.WorkerSpec("t", factory, stable_after_s=999)
    task = asyncio.ensure_future(sup._supervise(ctx, spec))

    for _ in range(200):
        if len(attempts) >= 2:
            break
        await asyncio.sleep(0.005)

    assert len(attempts) >= 2, "unexpected return should be restarted"
    assert audit.errors == 0, "an unexpected return is not an error"
    assert ctx.restarts.get("t", 0) == 0, "restart counter only counts crashes"

    ctx.stop_event.set()
    block.set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_cancel_propagates_and_never_restarts():
    ctx = _ctx()
    audit: FakeAudit = ctx.audit  # type: ignore[assignment]
    started = asyncio.Event()

    async def factory(_ctx):
        async def _run():
            started.set()
            await asyncio.Event().wait()  # block forever

        return sup.WorkerHandle(run=_run(), stop=lambda: None)

    spec = sup.WorkerSpec("t", factory)
    task = asyncio.ensure_future(sup._supervise(ctx, spec))
    await asyncio.wait_for(started.wait(), 1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ctx.restarts.get("t", 0) == 0
    assert audit.errors == 0
    assert ctx._states["t"]["state"] == "stopped"


async def test_backoff_sleep_is_interrupted_by_stop_event(monkeypatch):
    # A large backoff proves the wrapper returns on stop_event, not after the
    # full sleep — i.e. SIGTERM during a backoff window exits promptly.
    monkeypatch.setattr(sup, "_BACKOFF_BASE", 100.0)
    ctx = _ctx()

    async def factory(_ctx):
        async def _run():
            raise RuntimeError("boom")

        return sup.WorkerHandle(run=_run(), stop=lambda: None)

    spec = sup.WorkerSpec("t", factory, stable_after_s=999)
    task = asyncio.ensure_future(sup._supervise(ctx, spec))
    await asyncio.sleep(0.05)  # let it crash and enter the 100 s backoff
    ctx.stop_event.set()
    # Must finish well within the 100 s backoff.
    await asyncio.wait_for(task, 1.0)


# ---------------------------------------------------------------------------
# channels adapter
# ---------------------------------------------------------------------------


async def test_channels_adapter_blocks_then_drains(monkeypatch):
    ctx = _ctx(db=object())

    class FakeTrigger:
        def __init__(self):
            self.stopped = False

        async def stop(self):
            self.stopped = True

    trig = FakeTrigger()
    health_cancelled = asyncio.Event()

    async def fake_start(db, only=None):
        assert db is ctx.db
        assert only is ctx.channel_only
        return [("lark", trig)]

    async def fake_health(started):
        assert started == [("lark", trig)]

        async def _hs():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                health_cancelled.set()
                raise

        return asyncio.ensure_future(_hs())

    monkeypatch.setattr(sup, "start_channel_triggers", fake_start)
    monkeypatch.setattr(sup, "start_channel_health_server", fake_health)

    handle = await sup._channels_factory(ctx)
    run_task = asyncio.ensure_future(handle.run)
    await asyncio.sleep(0.02)
    assert not run_task.done(), "channels adapter must block until stop_event"

    ctx.stop_event.set()  # handle.stop is exactly this
    await asyncio.wait_for(run_task, 1.0)

    assert trig.stopped, "each channel trigger must be stopped on shutdown"
    assert health_cancelled.is_set(), "health task must be cancelled+awaited"


async def test_channels_health_none_is_tolerated(monkeypatch):
    ctx = _ctx(db=object())

    async def fake_start(db, only=None):
        return []

    async def fake_health(started):
        return None  # fastapi/uvicorn absent -> no health server

    monkeypatch.setattr(sup, "start_channel_triggers", fake_start)
    monkeypatch.setattr(sup, "start_channel_health_server", fake_health)

    handle = await sup._channels_factory(ctx)
    run_task = asyncio.ensure_future(handle.run)
    await asyncio.sleep(0.02)
    ctx.stop_event.set()
    await asyncio.wait_for(run_task, 1.0)  # must not raise on health_task=None
