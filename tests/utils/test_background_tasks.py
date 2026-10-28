"""
@file_name: test_background_tasks.py
@date: 2026-08-14
@description: `spawn()` — the two halves of incident lesson #2, pinned.

A bare ``asyncio.create_task(coro)`` is a mine twice over:

1. The event loop holds only a WEAK reference (``asyncio.all_tasks``), so a
   task suspended on an await can be collected mid-flight and simply never
   finish — silently, with no traceback.
2. An exception raised inside it surfaces only as a "Task exception was never
   retrieved" warning whenever GC gets around to it.

What these tests can and cannot prove: forcing the collector to actually take a
suspended Task is not portably reproducible (the Event holds the future, which
holds the task's ``__wakeup``), so no test here demonstrates a real collection.
What they DO pin is that a task with no caller-side reference stays tracked,
runs to completion, and reports its exception — which is the property the strong
set exists to provide. Removing ``_TASKS.add`` fails four of the six.
"""
from __future__ import annotations

import asyncio
import gc

import pytest
from loguru import logger

from narranexus.platform.utils.background_tasks import spawn, pending, drain


@pytest.fixture
def log_lines():
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


async def test_spawn_tracks_then_releases():
    gate = asyncio.Event()
    done: list[str] = []

    async def body() -> None:
        await gate.wait()
        done.append("ran")

    task = spawn(body(), name="tracked")
    await asyncio.sleep(0)
    assert task in pending()

    gate.set()
    await task
    # The done-callback runs on the next loop pass, not inline with `await`.
    await asyncio.sleep(0)
    assert task not in pending()
    assert done == ["ran"]


async def test_an_unreferenced_task_still_completes_after_a_collection():
    """The shape the helper is for: nobody holds this task but ``spawn``.

    The forced collection is a best-effort nudge, not a guarantee the task
    would otherwise have been taken — see the module docstring. The assertion
    that carries weight is that a task with no caller-side reference is still
    reachable, still tracked, and still runs.
    """
    gate = asyncio.Event()
    finished: list[str] = []

    async def body() -> None:
        await gate.wait()
        finished.append("survived")

    spawn(body(), name="gc-victim")  # deliberately not bound to a name
    await asyncio.sleep(0)

    gc.collect()
    gc.collect()

    gate.set()
    await drain(timeout=2.0)
    assert finished == ["survived"], "the task was collected mid-flight"


async def test_a_raising_task_logs_at_error_and_is_released(log_lines):
    async def body() -> None:
        raise RuntimeError("boom in the background")

    task = spawn(body(), name="exploder")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert task.done()
    assert task not in pending()
    joined = "\n".join(log_lines)
    assert "exploder" in joined
    assert "boom in the background" in joined


async def test_a_cancelled_task_is_not_reported_as_a_failure(log_lines):
    """Cancellation is how shutdown works, not a fault worth an ERROR line."""
    gate = asyncio.Event()

    async def body() -> None:
        await gate.wait()

    task = spawn(body(), name="cancelled-one")
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert task not in pending()
    assert not [ln for ln in log_lines if "ERROR" in ln and "cancelled-one" in ln]


async def test_drain_waits_for_everything_in_flight():
    order: list[int] = []

    async def body(n: int) -> None:
        await asyncio.sleep(0.01 * n)
        order.append(n)

    for n in (3, 1, 2):
        spawn(body(n), name=f"drain-{n}")

    await drain(timeout=2.0)
    assert sorted(order) == [1, 2, 3]
    assert pending() == frozenset()


async def test_drain_is_bounded_and_leaves_the_stragglers_tracked():
    """A wedged task must not make ``drain`` hang forever — it is a test and
    shutdown convenience, never a guarantee that the work finished."""
    gate = asyncio.Event()

    async def body() -> None:
        await gate.wait()

    task = spawn(body(), name="straggler")
    await drain(timeout=0.05)
    assert not task.done()
    assert task in pending()

    gate.set()
    await drain(timeout=1.0)
    assert task.done()

# --- loop scoping: the reason drain() stopped being process-wide ------------

def test_pending_outside_a_running_loop_is_empty():
    """A sync caller has no loop, so it has nothing meaningful to report.

    Returning the whole set here would hand back other loops' corpses.
    """
    assert pending() == frozenset()


async def test_a_task_from_a_closed_loop_is_neither_reported_nor_waited_on():
    """The whole point of the loop filter, stated as the failure it prevents.

    `spawn` now sits under `DataLoader._schedule_dispatch`, which most of the
    suite touches indirectly. pytest-asyncio gives every test its own loop, so a
    dispatch still in flight when a loop closes never runs its done-callback and
    stays in `_TASKS` forever. Before the filter that corpse (a) failed another
    file's `pending() == frozenset()` with evidence pointing at the wrong test,
    and (b) made `drain()` burn its entire timeout waiting on something that can
    never complete.

    The ghost is built on a THREAD: a second loop cannot be driven from inside a
    running one.
    """
    import threading

    from narranexus.platform.utils import background_tasks as _bt

    box: dict[str, asyncio.Task] = {}

    def _make_ghost() -> None:
        other = asyncio.new_event_loop()
        asyncio.set_event_loop(other)

        async def _spawn_and_park():
            box["task"] = spawn(asyncio.Event().wait(), name="ghost")
            await asyncio.sleep(0)  # let it start, never let it finish

        other.run_until_complete(_spawn_and_park())
        other.close()

    th = threading.Thread(target=_make_ghost)
    th.start()
    th.join()

    ghost = box["task"]
    assert ghost in _bt._TASKS, "precondition: the corpse really is still tracked"

    try:
        assert ghost not in pending(), "a dead loop's task leaked into pending()"

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await drain(timeout=2.0)
        assert loop.time() - t0 < 0.5, (
            "drain() waited on a task whose loop is closed — it can never be "
            "satisfied, so the whole timeout burns on every call"
        )
    finally:
        # This test would otherwise BE the pollution it exists to prevent.
        _bt._TASKS.discard(ghost)
