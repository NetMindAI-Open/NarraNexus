"""
@file_name: admission.py
@author:
@date: 2026-06-17
@description: Two-level concurrency admission control for agent runs.

A single user can drive MANY agents at once (chat + scheduled jobs +
message-bus interactions), so without a gate the box OOMs. This is the
control-plane gate that bounds it (binding rule #14 compliant: it only
ever DELAYS the start of a run by queueing — it NEVER interrupts a
running loop).

Two caps + a memory guard (all env-tunable, calibrated for a 64G host):
  - MAX_CONCURRENT_USERS   (global)  — distinct users with ≥1 active loop
  - MAX_LOOPS_PER_USER     (per-user)— one user's simultaneous loops
  - MAX_CONCURRENT_LOOPS   (global)  — total loops; the real RAM ceiling
  - MIN_FREE_MEM_MB        (dynamic) — hold new loops when free RAM is low

A run is admitted only when ALL hold; otherwise it waits. The per-user
cap is the main anti-starvation lever (no user can exceed M); a fully
fair round-robin out-queue is a future refinement.

State lives behind this controller instance (a seam) so it can move to
Redis when the orchestrator scales to >1 replica (binding rule #20). For
now it is an in-process asyncio controller.

Disabled (all caps unlimited, no mem guard) in local/desktop so
``bash run.sh`` and the DMG behave exactly as before (binding rule #7);
enabled with the 64G defaults in cloud. Env vars override either way.
"""
from __future__ import annotations

import asyncio
import math
import os
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional


def _free_mem_mb() -> float:
    """Available RAM in MB, or +inf when it can't be read (non-Linux)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:  # noqa: BLE001
        pass
    return math.inf


def _opt_int_env(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else None  # 0 / negative = unlimited
    except ValueError:
        return default


class AgentAdmissionController:
    """In-process two-level admission gate (global + per-user + mem guard)."""

    def __init__(
        self,
        max_users: Optional[int],
        max_loops_per_user: Optional[int],
        max_loops_global: Optional[int],
        min_free_mem_mb: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_users = max_users
        self.max_loops_per_user = max_loops_per_user
        self.max_loops_global = max_loops_global
        self.min_free_mem_mb = min_free_mem_mb
        self._clock = clock
        self._cond = asyncio.Condition()
        self._global = 0
        self._per_user: dict[str, int] = {}
        # user_id -> monotonic time it dropped to zero active loops. Present
        # ONLY while a user is idle; the executor reaper consumes this.
        self._idle_since: dict[str, float] = {}
        # Number of coroutines currently blocked inside acquire() waiting for
        # a slot — observable via snapshot() for L2 monitoring.
        self._waiting: int = 0

    @property
    def enabled(self) -> bool:
        return any(
            x is not None
            for x in (self.max_users, self.max_loops_per_user, self.max_loops_global)
        ) or self.min_free_mem_mb > 0

    def _active_users(self) -> int:
        return sum(1 for v in self._per_user.values() if v > 0)

    def _can_admit(self, user_id: str) -> bool:
        cur_user = self._per_user.get(user_id, 0)
        if self.max_loops_global is not None and self._global >= self.max_loops_global:
            return False
        if self.max_loops_per_user is not None and cur_user >= self.max_loops_per_user:
            return False
        if (
            self.max_users is not None
            and cur_user == 0
            and self._active_users() >= self.max_users
        ):
            return False
        if self.min_free_mem_mb > 0 and _free_mem_mb() < self.min_free_mem_mb:
            return False
        return True

    def snapshot(self) -> dict:
        """Return a plain-dict snapshot of current admission state.

        No lock is held — this is a best-effort monitoring read. Each
        field is read atomically (CPython GIL), so the snapshot is
        internally consistent enough for L2 observability; it is NOT
        transactionally consistent with an in-flight acquire/release.
        """
        free = _free_mem_mb()
        return {
            "active_users": self._active_users(),
            "active_loops": self._global,
            "queue_depth": self._waiting,
            "max_users": self.max_users,
            "max_loops_per_user": self.max_loops_per_user,
            "max_loops_global": self.max_loops_global,
            "min_free_mem_mb": self.min_free_mem_mb,
            "free_mem_mb": int(free) if free != math.inf else None,
            "per_user_loops": dict(self._per_user),
            "enabled": self.enabled,
        }

    async def acquire(self, user_id: str) -> str:
        """Wait (queue) until this run may start, then reserve a slot.

        Returns a token to pass back to ``release``. Never interrupts —
        only the START is delayed (binding rule #14).
        """
        async with self._cond:
            self._waiting += 1
            try:
                await self._cond.wait_for(lambda: self._can_admit(user_id))
            finally:
                self._waiting -= 1
            self._global += 1
            self._per_user[user_id] = self._per_user.get(user_id, 0) + 1
            self._idle_since.pop(user_id, None)  # active again → not idle
        return user_id

    async def release(self, token: str) -> None:
        async with self._cond:
            self._global = max(0, self._global - 1)
            if token in self._per_user:
                self._per_user[token] -= 1
                if self._per_user[token] <= 0:
                    del self._per_user[token]
                    self._idle_since[token] = self._clock()  # went idle now
            self._cond.notify_all()

    async def claim_idle_users(self, ttl_seconds: float) -> list[str]:
        """Atomically return + un-track users idle for >= ttl_seconds.

        A user is "idle" once its active-loop count hits zero (stamped in
        release). Returned users are removed from idle tracking under the
        lock so the reaper can stop their executor without double-reaping;
        if a new run arrives afterwards the broker just cold-starts a fresh
        container. Users with active loops are never returned (rule #14 —
        we never reap a running loop).
        """
        async with self._cond:
            now = self._clock()
            ready = [u for u, ts in self._idle_since.items() if now - ts >= ttl_seconds]
            for u in ready:
                del self._idle_since[u]
            return ready

    @asynccontextmanager
    async def slot(self, user_id: str):
        token = await self.acquire(user_id)
        try:
            yield
        finally:
            await self.release(token)


_controller: Optional[AgentAdmissionController] = None


def _build_from_env() -> AgentAdmissionController:
    """Cloud → 64G-calibrated defaults; local/desktop → unlimited (rule #7).
    Env vars override in either mode."""
    try:
        from xyz_agent_context.utils.deployment_mode import get_deployment_mode
        is_cloud = get_deployment_mode() == "cloud"
    except Exception:  # noqa: BLE001
        is_cloud = False

    if is_cloud:
        d_users, d_per_user, d_global, d_mem = 20, 5, 50, 6144
    else:
        d_users, d_per_user, d_global, d_mem = None, None, None, 0

    return AgentAdmissionController(
        max_users=_opt_int_env("MAX_CONCURRENT_USERS", d_users),
        max_loops_per_user=_opt_int_env("MAX_LOOPS_PER_USER", d_per_user),
        max_loops_global=_opt_int_env("MAX_CONCURRENT_LOOPS", d_global),
        min_free_mem_mb=int(os.environ.get("MIN_FREE_MEM_MB", str(d_mem)) or d_mem),
    )


def get_admission_controller() -> AgentAdmissionController:
    """Process-wide singleton (the seam that could become Redis-backed)."""
    global _controller
    if _controller is None:
        _controller = _build_from_env()
    return _controller


def reset_admission_controller_for_test(controller: Optional[AgentAdmissionController] = None) -> None:
    """Test hook — inject a controller or clear the singleton."""
    global _controller
    _controller = controller
