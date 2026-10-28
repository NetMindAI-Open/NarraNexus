"""
@file_name: cancel_watcher.py
@author:
@date: 2026-08-07
@description: Cross-process delivery of "stop this run" to the token holder.

A CancellationToken only reaches the run that owns it, in the process that
owns it. Chat stop works because the frontend, the token and the run share
one backend process (websocket.py holds them in app.state.active_runs).
Bus-driven runs live in the workers process, which the HTTP request never
enters — so the stop request travels through the DB instead
(``events.cancel_requested_at``), and this watcher is the piece that reads
it on the far side and fires the local token.

DB as the medium is the house style for cross-process run facts: the
liveness heartbeat, the stale sweep and the observation endpoint's
tail-follow all mediate through ``events`` rather than talking to each
other. An internal control port on the workers process was the alternative
and loses on multi-process topology, service discovery and auth.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Dict, Optional

from loguru import logger

from narranexus.platform.agent_runtime.cancellation import CancellationToken
from narranexus.platform.utils.run_liveness import parse_db_utc

# How often the registry is reconciled against the DB. One second is the
# budget the user actually feels: the click already got its own instant
# acknowledgement from the endpoint, so this only bounds how long the run
# keeps burning tokens after that.
DEFAULT_POLL_INTERVAL_S = 1.0

CANCEL_REASON = "Owner requested stop"


class CancelWatcher:
    """Watches the DB for stop requests aimed at runs living in THIS process.

    One instance per process (see ``get_cancel_watcher``), holding
    ``{run_id: token}``. Every tick is a single batched query over the
    registered ids — not one query per run — so a process driving N
    concurrent runs still costs one round trip per second.

    Deliberately NOT part of RunRecorder. The recorder's contract is to be a
    pure observer that can never affect the run it records (binding rule
    #14); this class exists to stop runs. Same table, opposite mandate —
    folding them together would put an interruption path inside the
    component whose guarantee is that it never interrupts.

    **Event-loop affinity**: ``CancellationToken`` wraps an ``asyncio.Event``,
    which is only safe within the loop that created it. Both the poll task and
    the tokens must therefore belong to one loop — true for every caller today
    (the trigger registers from the same loop it runs the agent on). A future
    caller driving runs on a second loop needs its own watcher instance there.
    """

    def __init__(
        self,
        db: Any,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._db = db
        self._poll_interval_s = poll_interval_s
        self._tokens: Dict[str, CancellationToken] = {}
        self._task: Optional[asyncio.Task] = None

    # ===== Registry =====

    def register(self, run_id: str, token: CancellationToken) -> None:
        """Start watching ``run_id``. Idempotent; starts the poll loop lazily.

        Called from the trigger's ``on_event_id`` callback — the earliest
        moment the run has an id at all (Step 0 mints it). Anything before
        that has nothing to key a stop request on.
        """
        if not run_id:
            return
        self._tokens[run_id] = token
        self._ensure_task()

    def unregister(self, run_id: str) -> None:
        """Stop watching ``run_id``. Safe to call for an unknown id."""
        self._tokens.pop(run_id, None)

    @property
    def watching(self) -> bool:
        """Whether any run is currently watched."""
        return bool(self._tokens)

    @property
    def running(self) -> bool:
        """Whether the poll loop is alive."""
        return self._task is not None and not self._task.done()

    # ===== Polling =====

    async def poll_once(self) -> int:
        """Reconcile the registry against the DB once. Returns tokens fired.

        Every failure path degrades to "no stop pending" and keeps the
        registry intact: an unreadable DB means the answer is UNKNOWN, and
        the one thing this must never do is take down a run that is working
        fine because the watcher had a bad second.
        """
        run_ids = list(self._tokens.keys())
        if not run_ids:
            return 0

        try:
            placeholders = ",".join(["%s"] * len(run_ids))
            rows = await self._db.execute(
                f"SELECT event_id, started_at, cancel_requested_at FROM events WHERE event_id IN ({placeholders})",
                tuple(run_ids),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[cancel-watcher] lookup failed, treating as no-op: {e}")
            return 0

        fired = 0
        for row in rows or []:
            run_id = row.get("event_id")
            token = self._tokens.get(run_id)
            if token is None:
                continue
            if not self._stop_applies(row):
                continue
            try:
                token.cancel(CANCEL_REASON)
                fired += 1
                logger.info(f"[cancel-watcher] stop delivered to run {run_id}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[cancel-watcher] token.cancel failed for {run_id}: {e}")
            finally:
                # Fired or not, this run is done being watched: the token is
                # latched (cancel is idempotent) and the run is on its way to
                # a terminal row.
                self.unregister(run_id)
        return fired

    @staticmethod
    def _stop_applies(row: dict) -> bool:
        """Whether this row's stop request belongs to the run now in flight.

        The flag lives on a long-lived row, so "flag is set" alone is not
        enough — it must have been raised AFTER the current run started, or a
        request that landed while a previous run was finishing would kill its
        successor. When ``started_at`` is missing or unparseable we honour the
        request anyway: the owner explicitly asked to stop, and refusing on a
        technicality reproduces the 8-minute black box this whole feature
        exists to remove.
        """
        requested = parse_db_utc(row.get("cancel_requested_at"))
        if requested is None:
            return False
        started = parse_db_utc(row.get("started_at"))
        if started is None:
            return True
        return requested >= started

    # ===== Loop =====

    def _ensure_task(self) -> None:
        if self.running:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop yet (sync construction in a test). The next register()
            # from inside a loop starts it.
            return
        self._task = loop.create_task(self._run_loop())
        # Never a bare create_task: an exception in a fire-and-forget task is
        # only reported during GC, and this one is the reason a stop lands at
        # all (incident lesson #2).
        self._task.add_done_callback(self._on_task_done)

    @staticmethod
    def _on_task_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"[cancel-watcher] poll loop died: {exc!r}")

    async def _run_loop(self) -> None:
        """Poll until nothing is watched, then retire.

        Retiring on an empty registry keeps an idle process (no bus traffic
        for hours) from holding a per-second query open forever; the next
        register() brings the loop back.
        """
        while self._tokens:
            try:
                await self.poll_once()
            except Exception as e:  # noqa: BLE001
                # poll_once already swallows its own failures; this is the
                # backstop that keeps the loop alive if it ever doesn't.
                logger.warning(f"[cancel-watcher] poll raised: {e!r}")
            await asyncio.sleep(self._poll_interval_s)

    async def aclose(self) -> None:
        """Stop the loop and drop the registry (process shutdown)."""
        self._tokens.clear()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            # The await is only here to let the cancellation land; both the
            # CancelledError we just caused and any late failure inside the loop
            # are equally uninteresting at shutdown.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


# ===== Process-wide accessor =====

_watcher: Optional[CancelWatcher] = None


def get_cancel_watcher(db: Any) -> CancelWatcher:
    """The process's watcher, created on first use.

    A singleton so one poll loop serves every run in the process. ``db`` is
    only used on the first call.
    """
    global _watcher
    if _watcher is None:
        _watcher = CancelWatcher(db)
    return _watcher


def reset_cancel_watcher() -> None:
    """Drop the singleton (tests)."""
    global _watcher
    _watcher = None
