"""
@file_name: background_tasks.py
@author: NarraNexus
@date: 2026-08-14
@description: spawn() — fire-and-forget that can neither be collected nor go silent.

Incident lesson #2 names two separate mines in ``asyncio.create_task(coro)``
when nobody holds the returned Task:

1. **It can vanish.** The event loop keeps only a WEAK reference (that is what
   ``asyncio.all_tasks`` is), so a task suspended on an await can be garbage
   collected mid-flight. It does not raise, it does not log — the work simply
   never finishes. In practice whatever the task awaits usually keeps it alive,
   which is exactly why this failure mode is rare, undebuggable, and worth
   designing out rather than reasoning about case by case.
2. **Its exception is deferred to GC.** An unretrieved exception surfaces as a
   "Task exception was never retrieved" warning whenever the collector gets
   around to it, which is to say: not at the moment it matters, and often not
   in the same log file.

``spawn`` closes both. The task joins ``_TASKS`` until it settles, and a
done-callback logs any exception at ERROR the instant it lands.

This is deliberately NOT a supervisor: it does not retry, does not restart, and
does not cancel anything on a schedule. A detached hook that takes an hour is a
legitimate workload (binding rule #14) — the only thing being fixed here is our
own ability to lose it.

``pending()`` / ``drain()`` exist so tests can await what a run left behind
instead of sleeping and hoping. Both are scoped to the CURRENT event loop —
see ``pending()`` for why that matters under pytest-asyncio. ``drain`` is bounded and makes no promise that
the work completed; check ``pending()`` afterwards if that matters.

Callers that need more than a log line on failure (an owner-facing notice, an
audit row) keep their own try/except INSIDE the coroutine — see
``agent_runtime._run_hooks_background``, which alerts on credential errors.
That belongs to the caller's domain, not here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, FrozenSet, Optional

from loguru import logger

# Strong references to in-flight detached tasks. A task is added on spawn and
# removed by its own done-callback, so this stays bounded by concurrency, not
# by total tasks ever spawned.
_TASKS: set[asyncio.Task] = set()


def _on_done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        # Cancellation is how shutdown works. Reporting it as a failure would
        # make every clean stop look like an incident (lesson #3: an alarm that
        # cries during normal operation is an alarm nobody reads).
        return
    exc = task.exception()
    if exc is not None:
        name = task.get_name()
        logger.opt(exception=exc).error(
            f"[background] task {name!r} died: {exc!r}"
        )


def spawn(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str,
) -> asyncio.Task:
    """Start ``coro`` detached, tracked, and loud on failure.

    Args:
        coro: The coroutine to run. Ownership transfers here.
        name: Task name, used in the failure log line. Make it greppable —
            this string is the only thing identifying the task once it dies.

    Returns:
        The Task. Callers may await it, but are not required to; the point of
        this helper is that ignoring the return value is safe.
    """
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def pending() -> FrozenSet[asyncio.Task]:
    """Tasks still in flight ON THE CURRENT EVENT LOOP.

    Loop-scoped, not process-scoped. `_TASKS` is a module global while asyncio
    loops are not: a task whose loop closed before it finished never runs its
    done-callback, so it stays in the set forever. In production there is one
    long-lived loop and this never comes up; under pytest-asyncio every test
    gets its own, and `spawn` now sits under `DataLoader._schedule_dispatch` —
    the batch entry point most of the suite touches indirectly. Without this
    filter, one test's leftover makes another file's `pending() == frozenset()`
    fail, with evidence pointing at the wrong culprit.

    Outside a running loop (a sync caller) there is nothing meaningful to
    report, so the answer is empty rather than "everything, including corpses".
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return frozenset()
    return frozenset(t for t in _TASKS if t.get_loop() is loop)


async def drain(timeout: Optional[float] = None) -> None:
    """Wait for currently-tracked tasks to settle, at most ``timeout`` seconds.

    Bounded on purpose: a wedged detached task must not turn a test teardown or
    a shutdown path into a hang. Nothing is cancelled — anything still running
    when the budget runs out stays tracked and keeps going. Tasks spawned WHILE
    draining are not waited on; call again if that matters.
    """
    # `pending()`, not `_TASKS`: waiting on a task parked on a CLOSED loop can
    # never be satisfied, so it would burn the whole timeout every call and
    # blame the wrong code.
    in_flight = list(pending())
    if not in_flight:
        return
    await asyncio.wait(in_flight, timeout=timeout)
    # Done-callbacks are scheduled, not inline: yield once so `pending()` is
    # accurate for whoever we return to.
    await asyncio.sleep(0)
