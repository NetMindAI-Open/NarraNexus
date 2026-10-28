"""Backend shutdown must preserve detached runs and their finalization resources."""

import asyncio
import inspect
from unittest.mock import AsyncMock

import anyio
import pytest

from backend.run_lifecycle import RunTasks


@pytest.mark.asyncio
async def test_drain_retains_run_before_id_assignment_and_awaits_finalizer():
    runs = RunTasks()
    work = asyncio.Event()
    finalizing = asyncio.Event()
    finalized = asyncio.Event()
    cleanup = AsyncMock()

    async def run():
        try:
            await work.wait()
        finally:
            finalizing.set()
            await finalized.wait()

    task = runs.start(run())
    closing = asyncio.create_task(runs.close(cleanup))
    await asyncio.sleep(0)
    assert not closing.done()
    cleanup.assert_not_awaited()
    work.set()
    await finalizing.wait()
    cleanup.assert_not_awaited()
    assert not task.cancelled()
    finalized.set()
    await closing
    assert task.done() and not task.cancelled()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_shutdown_cancellation_does_not_cancel_run_or_cleanup():
    runs = RunTasks()
    work = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_done = asyncio.Event()

    async def cleanup():
        cleanup_started.set()
        await cleanup_done.wait()

    task = runs.start(work.wait())
    closing = asyncio.create_task(runs.close(cleanup))
    await asyncio.sleep(0)
    for _ in range(2):
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        assert not task.done()
        assert not cleanup_started.is_set()
    work.set()
    await cleanup_started.wait()
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    cleanup_done.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert not task.cancelled()


@pytest.mark.asyncio
async def test_anyio_cancel_scope_still_drains():
    runs = RunTasks()
    cleanup = AsyncMock()
    task = runs.start(asyncio.sleep(0))
    with anyio.CancelScope() as scope:
        scope.cancel()
        await runs.close(cleanup)
    assert task.done() and not task.cancelled()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_and_cancelled_runs_do_not_abandon_other_runs(monkeypatch):
    import backend.run_lifecycle as mod

    runs = RunTasks()
    reports = []
    monkeypatch.setattr(mod.logger, "error", lambda *args, **kwargs: reports.append(args))
    cleanup = AsyncMock()
    work = asyncio.Event()

    async def broken():
        raise ValueError("synthetic failure")

    failed = runs.start(broken())
    cancelled = runs.start(asyncio.sleep(10))
    cancelled.cancel()
    remaining = runs.start(work.wait())
    closing = asyncio.create_task(runs.close(cleanup))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not remaining.done()
    assert not closing.done()
    cleanup.assert_not_awaited()
    assert reports
    work.set()
    await closing

    assert isinstance(failed.exception(), ValueError)
    assert cancelled.cancelled()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_housekeeping_finally_is_joined_before_database_close(monkeypatch):
    from fastapi import FastAPI
    import backend.main as main

    app = FastAPI()
    started = asyncio.Event()
    finalizing = asyncio.Event()
    finished = asyncio.Event()
    db_close = AsyncMock()
    monkeypatch.setattr(main, "close_db_client", db_close)
    monkeypatch.setattr("backend.plugins_host.stop_backend_workers", AsyncMock())

    async def housekeeping():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalizing.set()
            await finished.wait()

    app.state.stale_run_sweep_task = asyncio.create_task(housekeeping())
    await started.wait()
    closing = asyncio.create_task(main._shutdown_resources(app))
    await finalizing.wait()
    db_close.assert_not_awaited()
    finished.set()
    await closing
    db_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_stop_error_still_releases_other_resources(monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    import backend.main as main

    app = FastAPI()
    app.state.memory_consolidation_worker = SimpleNamespace(
        stop=AsyncMock(side_effect=ValueError("worker stop failed")),
    )
    plugin_stop = AsyncMock()
    db_close = AsyncMock()
    monkeypatch.setattr(main, "close_db_client", db_close)
    monkeypatch.setattr("backend.plugins_host.stop_backend_workers", plugin_stop)
    with pytest.raises(Exception, match="worker stop failed|shutdown"):
        await main._shutdown_resources(app)
    plugin_stop.assert_awaited_once_with(app)
    db_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_is_shared_and_refuses_new_runs_without_leaking_coroutines():
    runs = RunTasks()
    cleanup = AsyncMock()
    work = asyncio.Event()
    runs.start(work.wait())
    closing = asyncio.create_task(runs.close(cleanup))
    await asyncio.sleep(0)
    refused = work.wait()
    with pytest.raises(RuntimeError, match="shutting down"):
        runs.start(refused)
    assert inspect.getcoroutinestate(refused) == inspect.CORO_CLOSED
    second = asyncio.create_task(runs.close(cleanup))
    work.set()
    await asyncio.gather(closing, second)
    await runs.close(cleanup)
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_failure_is_propagated_after_drain():
    runs = RunTasks()
    task = runs.start(asyncio.sleep(0))
    cleanup = AsyncMock(side_effect=ValueError("cleanup failed"))
    with pytest.raises(ValueError, match="cleanup failed"):
        await runs.close(cleanup)
    assert task.done() and not task.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_job_execution_remains_owned_after_client_disconnect(monkeypatch, stream):
    from types import SimpleNamespace
    import backend.routes.openai_compat as compat

    runs = RunTasks()
    started = asyncio.Event()
    finished = asyncio.Event()
    work = asyncio.Event()
    cleanup = AsyncMock()

    async def job(*args):
        started.set()
        await work.wait()
        finished.set()
        return SimpleNamespace(as_text=lambda: "complete")

    monkeypatch.setattr(compat, "execute_job_once", job)
    handler = asyncio.create_task(compat._run_job_completion(
        agent_id="synthetic", job_id="synthetic", stream=stream, run_tasks=runs,
    ))
    await started.wait()
    if stream:
        response = await handler
        await anext(response.body_iterator)
        await response.body_iterator.aclose()
    else:
        handler.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handler
    closing = asyncio.create_task(runs.close(cleanup))
    await asyncio.sleep(0)
    assert not closing.done()
    cleanup.assert_not_awaited()
    work.set()
    await closing
    assert finished.is_set()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_managed_after_run_audit_is_drained(monkeypatch):
    from types import SimpleNamespace
    from backend.routes.openai_compat import _schedule_managed_after_run

    runs = RunTasks()
    work = asyncio.Event()
    after_run = AsyncMock(side_effect=work.wait)
    cleanup = AsyncMock()
    _schedule_managed_after_run(runs, SimpleNamespace(after_run=after_run))
    closing = asyncio.create_task(runs.close(cleanup))
    await asyncio.sleep(0)
    cleanup.assert_not_awaited()
    work.set()
    await closing
    after_run.assert_awaited_once()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_reporting_interval_never_cancels_work(monkeypatch):
    import backend.run_lifecycle as mod

    monkeypatch.setattr(mod, "_DRAIN_FIRST_REPORT_SECONDS", 0.001)
    monkeypatch.setattr(mod, "_DRAIN_REPORT_SECONDS", 0.001)
    runs = RunTasks()
    work = asyncio.Event()
    cleanup = AsyncMock()
    task = runs.start(work.wait())
    closing = asyncio.create_task(runs.close(cleanup))
    await asyncio.sleep(0.02)
    assert not task.done() and not closing.done()
    cleanup.assert_not_awaited()
    work.set()
    await closing


@pytest.mark.asyncio
async def test_first_drain_report_lands_inside_a_container_grace_period(monkeypatch):
    """Docker's default stop grace is 10 s. A 30 s first report means a held-up
    shutdown is killed before it ever says why; the first one must come sooner,
    then settle into the steady cadence."""
    import backend.run_lifecycle as mod

    assert mod._DRAIN_FIRST_REPORT_SECONDS < 10 <= mod._DRAIN_REPORT_SECONDS
    timeouts = []
    real_wait = asyncio.wait
    work = asyncio.Event()

    async def recording_wait(tasks, *, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 3:
            work.set()
        return await real_wait(tasks, timeout=0.001)

    monkeypatch.setattr(mod.asyncio, "wait", recording_wait)
    runs = RunTasks()
    runs.start(work.wait())
    await runs.close(AsyncMock())
    assert timeouts[:3] == [mod._DRAIN_FIRST_REPORT_SECONDS, mod._DRAIN_REPORT_SECONDS, mod._DRAIN_REPORT_SECONDS]


@pytest.mark.asyncio
async def test_database_finalization_and_heartbeat_survive_drain(db_client, monkeypatch):
    from narranexus.platform.agent_runtime.background_run import BackgroundRun
    import narranexus.platform.agent_runtime.run_recorder as recorder_mod

    monkeypatch.setattr(recorder_mod, "HEARTBEAT_INTERVAL_S", 0.01)
    runs = RunTasks()
    active_runs = {}
    work = asyncio.Event()
    bound = asyncio.Event()
    bg = BackgroundRun(
        agent_id="", user_id="synthetic", input_preview="", db=db_client,
        active_runs=active_runs,
    )

    async def run():
        await db_client.insert("events", {
            "event_id": "evt_shutdown_test", "agent_id": "", "user_id": "synthetic",
            "trigger": "chat", "trigger_source": "test", "state": "completed",
        })
        await bg.emit({"type": "progress", "step": "0", "status": "completed",
                       "details": {"event_id": "evt_shutdown_test"}})
        await bg.emit({"type": "agent_thinking", "thinking_content": "final text"})
        bound.set()
        try:
            await work.wait()
            bg.state = "completed"
        finally:
            await bg._finalize()

    async def cleanup():
        row = await db_client.get_one("events", {"event_id": "evt_shutdown_test"})
        assert row["state"] == "completed" and row["finished_at"]
        stream = await db_client.get("event_stream", {"event_id": "evt_shutdown_test"})
        assert any(item["payload"] == "final text" for item in stream)
        assert not active_runs
        assert bg.broadcaster.is_closed

    bg.task = runs.start(run())
    closing = asyncio.create_task(runs.close(cleanup))
    await bound.wait()
    first = await db_client.get_one("events", {"event_id": bg.run_id})
    for _ in range(100):
        row = await db_client.get_one("events", {"event_id": bg.run_id})
        if row["last_event_at"] != first["last_event_at"]:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("heartbeat stopped while shutdown drained")
    assert row["state"] == "running"
    assert not closing.done()
    work.set()
    await closing
