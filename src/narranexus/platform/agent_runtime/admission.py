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
now it is an in-process asyncio controller — which means its view is
PARTIAL: the cloud orchestrator runs as backend + workers, so neither
process's counters know about the other's runs. Harmless for admission
(each process caps its own share) but NOT for the idle bookkeeping the
reaper consumes, which is why ``claim_idle_users`` takes an
out-of-process ``is_busy`` veto.

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
from typing import Awaitable, Callable, Optional

from loguru import logger

# Injected cross-process veto for idle claiming: "is this user busy
# somewhere else?". See claim_idle_users.
BusyCheck = Callable[[str], Awaitable[bool]]

# Whole-batch budget for the veto. A hung DB pool would otherwise park
# claim_idle_users forever, and with it the reaper loop — silently, with no
# exception for its done-callback to report (incident lesson #4). Candidates
# left unjudged when it runs out read as busy, so a wedged DB stalls culling
# rather than the reaper. A budget for the CHECK, never a ceiling on anything
# a user's agent does.
_VETO_BUDGET_S = 60.0


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

    async def restamp_idle(self, user_id: str) -> None:
        """Re-mark a user idle after a claim that was not acted on.

        ``claim_idle_users`` is destructive; a caller that claims and then
        decides not to stop the executor would otherwise drop the stamp for
        good (see that method's docstring for why that leaks). The user waits
        a fresh TTL, which is truthful — it was busy a moment ago.

        setdefault, not assignment: a release may have landed while the caller
        was backing off, and that stamp is both older and truthful. Nothing to
        preserve after a claim (the claim deleted it), everything to preserve
        after a concurrent release.
        """
        async with self._cond:
            if user_id not in self._per_user:
                self._idle_since.setdefault(user_id, self._clock())

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

    async def claim_idle_users(
        self,
        ttl_seconds: float,
        is_busy: Optional[BusyCheck] = None,
        per_check_budget: float = 0.0,
    ) -> list[str]:
        """Return + un-track users idle for >= ttl_seconds. Claiming is
        DESTRUCTIVE — a returned user loses its idle stamp.

        A user is "idle" once THIS PROCESS's active-loop count hits zero
        (stamped in release). Returned users are removed from idle tracking
        under the lock so the reaper can stop their executor without
        double-reaping; if a new run arrives afterwards the broker just
        cold-starts a fresh container. Users with active loops are never
        returned (rule #14 — we never reap a running loop).

        ``is_busy`` is the CROSS-PROCESS veto. This controller is a
        per-process singleton, so "zero active loops here" does not mean the
        user is idle — backend cannot see runs alive in workers and vice
        versa. The caller injects an out-of-process truth source (the reaper
        passes ``executor_reaper._CullVeto``, which wraps the DB lookup, folds
        the blocking run id down to a bool and de-duplicates its audit rows
        per run); any user it vetoes is skipped and KEEPS its idle stamp.

        Keeping the stamp is why the veto lives in here rather than in the
        caller's filter: claiming is destructive, so a caller that
        claimed-then-skipped would drop the stamp and the user would not be
        reconsidered until its next release in THIS process — for a user
        driven mostly from another process that is never, and its container
        leaks forever. Trading one silent failure for another is not a fix.

        ``per_check_budget`` is the caller's own per-check timeout, if it has
        one. Passing it stops this batch from ever cutting a check short: a
        check is simply not started unless the whole budget still fits. That
        matters because the caller's timeout is where the ACCOUNTING lives
        (the reaper counts a timed-out check as "could not tell"), so a check
        killed by the outer budget instead is one that vanishes from the
        tally. Told, not imported: this layer must not depend on the reaper.

        The veto runs OUTSIDE the lock (it does I/O — the reaper's hits the
        DB) so admission never stalls behind it; the second pass re-checks
        that each candidate's stamp is still the one we saw, so a user that
        became active meanwhile is not claimed.
        """
        async with self._cond:
            now = self._clock()
            candidates = [
                (u, ts) for u, ts in self._idle_since.items() if now - ts >= ttl_seconds
            ]
        if not candidates:
            return []

        busy: set[str] = set()
        if is_busy is not None:
            # Sequential on purpose: one veto is one indexed read, and the
            # candidates are only those who crossed the TTL since the last
            # pass. A fan-out here would contend with live requests for the
            # same pool to save milliseconds nobody is waiting on.
            deadline = self._clock() + _VETO_BUDGET_S
            for user_id, _ in candidates:
                remaining = deadline - self._clock()
                try:
                    # Never start a check this batch cannot see through. The
                    # alternative — starting it and letting the outer wait_for
                    # cancel it — silently drops it from the caller's tally,
                    # and does so for the LAST candidate of every wedged pass,
                    # not occasionally. Held-back candidates keep their stamp
                    # and are reconsidered next pass.
                    if remaining <= 0 or remaining < per_check_budget:
                        raise TimeoutError("veto budget exhausted")
                    verdict = await asyncio.wait_for(is_busy(user_id), remaining)
                except Exception as e:  # noqa: BLE001 — no verdict ⇒ assume busy
                    logger.warning(f"[admission] busy veto failed user={user_id}: {e}")
                    verdict = True
                if verdict:
                    busy.add(user_id)

        async with self._cond:
            claimed: list[str] = []
            for user_id, stamp in candidates:
                if user_id in busy:
                    continue
                # Re-activated (acquire popped the stamp) or re-released
                # (release wrote a new one) while the veto was in flight.
                if self._idle_since.get(user_id) != stamp:
                    continue
                del self._idle_since[user_id]
                claimed.append(user_id)
            return claimed

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
        from narranexus.platform.utils.deployment_mode import get_deployment_mode
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
