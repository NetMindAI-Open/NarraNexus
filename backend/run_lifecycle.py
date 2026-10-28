"""Own detached request work until it settles, before lifespan releases resources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

import anyio
from loguru import logger

_T = TypeVar("_T")
#: First "still waiting" line, then the steady cadence. The first one has to
#: land inside a container's stop grace period (Docker's default is 10 s), or
#: operators never see that shutdown is held up by a live run before SIGKILL.
_DRAIN_FIRST_REPORT_SECONDS = 5.0
_DRAIN_REPORT_SECONDS = 30.0


class RunTasks:
    """Process-local ownership from task creation through terminal bookkeeping.

    The run-ID registry cannot provide this guarantee: a run waiting for
    admission or Step 0 has no ID yet. This owner never cancels run tasks.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._close_task: asyncio.Task[None] | None = None

    def start(self, work: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
        if self._close_task is not None:
            work.close()
            raise RuntimeError("Backend is shutting down; new runs are not accepted")
        try:
            task = asyncio.create_task(work)
        except BaseException:
            work.close()
            raise
        self._tasks.add(task)
        task.add_done_callback(self._settled)
        return task

    def _settled(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            logger.warning("[run-drain] Owned task was cancelled outside shutdown")
        elif (error := task.exception()) is not None:
            logger.error("[run-drain] Owned task failed: {!r}", error)

    async def close(self, cleanup: Callable[[], Awaitable[None]]) -> None:
        """Drain without a deadline; defer waiter cancellation through cleanup.

        Shielding only the run wait is insufficient: cancellation would let
        the lifespan return while the DB and finalizers are still in use.
        Concurrent callers share the entire drain and cleanup operation.
        """
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._drain_and_cleanup(cleanup))
        cancelled = False
        # AnyIO uses level cancellation; asyncio task.cancel() uses edge
        # cancellation. Protect against both without cancelling the runs.
        with anyio.CancelScope(shield=True):
            while not self._close_task.done():
                try:
                    await asyncio.shield(self._close_task)
                except asyncio.CancelledError:
                    cancelled = True
            self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _drain_and_cleanup(self, cleanup: Callable[[], Awaitable[None]]) -> None:
        logger.info("[run-drain] Waiting for {} owned tasks", len(self._tasks))
        report_after = _DRAIN_FIRST_REPORT_SECONDS
        while self._tasks:
            _, pending = await asyncio.wait(tuple(self._tasks), timeout=report_after)
            if pending:
                logger.info("[run-drain] Still waiting for {} owned tasks", len(pending))
            report_after = _DRAIN_REPORT_SECONDS
        logger.info("[run-drain] All owned tasks settled; releasing resources")
        await cleanup()
