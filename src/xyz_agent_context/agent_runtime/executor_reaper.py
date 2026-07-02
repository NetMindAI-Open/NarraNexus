"""
@file_name: executor_reaper.py
@author:
@date: 2026-06-17
@description: Idle-cull coordinator for per-user Executor containers.

Pure coordinator (dependency-injected): it owns neither the concurrency
state nor the docker transport. It periodically asks the admission
controller which users have gone idle past the TTL, and asks a ``stop_fn``
(the broker client) to stop them. This keeps the three concerns separate:
  - AgentAdmissionController — concurrency + idle bookkeeping
  - ExecutorReaper          — WHEN to cull (this file)
  - broker_client.stop_executor — HOW to stop (docker transport)

Binding rule #14: only idle executors (zero active loops) are ever
reaped — a running loop is never interrupted. The cull just delays the
next start by a cold boot, surfaced to the user via the "waking up" UX.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional

from loguru import logger

from xyz_agent_context.agent_runtime.admission import (
    AgentAdmissionController,
    get_admission_controller,
)

StopFn = Callable[[str], Awaitable[None]]

DEFAULT_IDLE_TTL_SEC = 1200   # 20 min (locked decision)
DEFAULT_INTERVAL_SEC = 120


class ExecutorReaper:
    """Periodically stops executors whose user has been idle past the TTL."""

    def __init__(
        self,
        controller: AgentAdmissionController,
        stop_fn: StopFn,
        *,
        ttl_seconds: float = DEFAULT_IDLE_TTL_SEC,
        interval_seconds: float = DEFAULT_INTERVAL_SEC,
    ) -> None:
        self._controller = controller
        self._stop_fn = stop_fn
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds

    async def reap_once(self) -> list[str]:
        """One cull pass. Returns the users whose executors were stopped.

        A stop failure for one user is logged and skipped (the broker's own
        label-based reaper backstops orphans); it never aborts the pass.
        """
        users = await self._controller.claim_idle_users(self.ttl_seconds)
        reaped: list[str] = []
        for user_id in users:
            try:
                await self._stop_fn(user_id)
                reaped.append(user_id)
            except Exception as e:  # noqa: BLE001 — best-effort, must not abort
                logger.warning(f"[reaper] failed to stop executor user={user_id}: {e}")
        if reaped:
            logger.info(f"[reaper] reaped {len(reaped)} idle executor(s): {reaped}")
        return reaped

    async def run_forever(self) -> None:
        logger.info(
            f"[reaper] started (ttl={self.ttl_seconds}s interval={self.interval_seconds}s)"
        )
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.reap_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[reaper] reap pass error: {e}")


def _on_reaper_done(task: "asyncio.Task") -> None:
    # Incident lesson #2: a fire-and-forget task must surface its death.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"[reaper] background task died: {exc!r}")


def maybe_start_executor_reaper() -> Optional["asyncio.Task"]:
    """Start the reaper as a background task — cloud + broker only.

    No-op (returns None) on local/desktop, or whenever no broker is
    configured: there are no per-user executors to cull there.
    """
    from xyz_agent_context.agent_framework.broker_client import broker_url, stop_executor

    if not broker_url():
        return None
    ttl = int(os.getenv("EXECUTOR_IDLE_TTL_SEC", "") or DEFAULT_IDLE_TTL_SEC)
    interval = int(os.getenv("EXECUTOR_REAP_INTERVAL_SEC", "") or DEFAULT_INTERVAL_SEC)
    reaper = ExecutorReaper(
        get_admission_controller(), stop_executor,
        ttl_seconds=ttl, interval_seconds=interval,
    )
    task = asyncio.create_task(reaper.run_forever())
    task.add_done_callback(_on_reaper_done)
    return task
