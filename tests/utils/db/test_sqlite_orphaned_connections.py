"""
@file_name: test_sqlite_orphaned_connections.py
@author: NarraNexus
@date: 2026-08-17
@description: A connection whose event loop died must not keep the database
hostage.

The failure this guards (found 2026-08-17 while profiling the suite):

  1. A short-lived event loop closes with a DB statement still in flight.
     This codebase mints such loops on purpose: `get_db_client_sync()` builds
     its client inside `asyncio.run(...)`, `lark_trigger` makes a fresh loop
     per WS reconnect, and one-shot scripts / migrations / this very test
     harness do the same. (NOT the MCP container — `module_runner.py` gives
     each module its own PROCESS with one process-lifetime `asyncio.run`.)
  2. The loop closes with that statement still in flight.
  3. aiosqlite's worker thread answers by calling `call_soon_threadsafe` on the
     closed loop, which raises; its `except` handler makes the SAME call again,
     raises again, uncaught — and the worker thread dies holding an open
     sqlite3 connection.
  4. Nobody can close that connection any more, so its lock is held for the
     life of the process. Every later writer waits out `busy_timeout` (30s) on
     each of `_MAX_WRITE_RETRIES` (10) attempts: ~321s, then
     "database is locked".

Six such collisions were 92% of the test suite's 38-minute wall clock, and
every one of them was reported as a PASSING test — which is exactly why this
file exists. The symptom is "slow", so nothing red ever pointed at it.

Both halves are asserted here, because either one alone leaves the hazard: the
worker thread has to survive (only it can close the connection), and
`db_factory` has to actually close what it evicts (nothing else will).

Timing is staged, never slept for: a blocker connection holds the write lock so
the statement is GUARANTEED to still be in flight when its loop closes. A repro
that races is a repro that goes green on its own the first time it is
inconvenient.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import aiosqlite
import pytest

from narranexus.platform.utils.db import db_backend_sqlite, db_factory
from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend


def _live_threads() -> set[str]:
    return {t.name for t in threading.enumerate() if t.is_alive()}


def test_the_aiosqlite_worker_patch_is_actually_installed():
    """The hardening is a module-import side effect, which is easy to lose in a
    refactor and impossible to notice: losing it costs 5 minutes per collision,
    not an exception."""
    assert (
        aiosqlite.core._connection_worker_thread
        is db_backend_sqlite._resilient_connection_worker_thread
    )


def test_the_worker_thread_is_a_daemon(tmp_path):
    """The other half of the same fix, and the half that is easy to drop.

    Upstream's worker is non-daemon and relied — by accident — on dying to let
    the process exit. Keeping it alive without this makes `threading._shutdown`
    join an orphan forever: measured 2026-08-17, a process with one connection
    orphaned behind a held write lock exits in 1.4s upstream and NEVER with the
    delivery patch alone. That lands on the desktop build as "quit leaves a
    zombie holding the DB file" and in containers as a SIGKILL on every stop.
    """
    async def _open() -> SQLiteBackend:
        backend = SQLiteBackend(str(tmp_path / "daemon.db"))
        await backend.initialize()
        return backend

    backend = asyncio.run(_open())
    try:
        assert backend._ensure_conn()._thread.daemon is True
    finally:
        asyncio.run(backend.close())


def test_delivering_to_a_closed_loop_is_a_no_op_not_a_raise():
    """Upstream re-raises from its own `except` handler, which is what kills the
    thread. Dropping the result is correct — the coroutine that was awaiting it
    died with its loop."""
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    loop.close()

    db_backend_sqlite._deliver_to_origin_loop(
        future, aiosqlite.core.set_result, "ignored"
    )
    db_backend_sqlite._deliver_to_origin_loop(
        future, aiosqlite.core.set_exception, RuntimeError("ignored")
    )


def test_a_write_abandoned_by_its_loop_leaves_the_connection_closable(tmp_path):
    """The whole failure on one database file, staged so it cannot race.

    A blocker holds the write lock, so the backend's INSERT is provably still
    parked inside sqlite when its loop goes away — no sleeping, no hoping: the
    worker having drained the request queue is the signal that it picked the
    statement up, and the blocker is what guarantees it cannot get past it.
    Releasing the blocker afterwards makes the worker deliver a result to a
    loop that no longer exists, which is the exact moment the thread used to
    die.
    """
    db_path = str(tmp_path / "abandoned.db")

    setup = sqlite3.connect(db_path, isolation_level=None)
    setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    setup.close()

    # Open the backend BEFORE anyone holds the lock: `initialize()` runs
    # `PRAGMA journal_mode=WAL`, which wants an exclusive lock and fails fast
    # rather than honouring busy_timeout.
    backend = SQLiteBackend(db_path)
    asyncio.run(backend.initialize())

    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # holds the write lock

    # Watch the handover directly. `conn._tx.empty()` cannot be the signal: an
    # empty queue means BOTH "the worker already took it" and "the task never
    # enqueued it", so waiting on emptiness would let the repro cancel a task
    # that was never in flight and still pass. Counting `put_nowait` is
    # unambiguous — and once the statement is handed over, the blocker holding
    # the write lock guarantees it cannot finish, whether it is still queued or
    # already in the worker's hands.
    conn = backend._ensure_conn()
    handed_over: list = []

    class _WatchedQueue:
        """Forwards to the SAME queue object the worker thread was started
        with (aiosqlite passes `_tx` in at construction), so substituting it
        here observes puts without rerouting anything."""

        def __init__(self, inner):
            self._inner = inner

        def put_nowait(self, item):
            handed_over.append(item)
            self._inner.put_nowait(item)

        def get(self, *args, **kwargs):
            return self._inner.get(*args, **kwargs)

        def empty(self):
            return self._inner.empty()

    conn._tx = _WatchedQueue(conn._tx)

    async def _a_loop_that_dies_mid_write() -> None:
        task = asyncio.create_task(
            backend.execute_write("INSERT INTO t (v) VALUES (?)", ("abandoned",))
        )
        # Bounded. An unbounded spin here would make the ONE test that
        # reproduces the original failure end-to-end fail as a 20-minute CI
        # timeout with no output — the same "a repro that hangs destroys the
        # evidence it exists to produce" trap that the daemon-thread half of
        # this fix was written for. The handover is the first thing
        # `execute_write` does after an uncontended lock, so a second is
        # already three orders of magnitude of slack.
        deadline = asyncio.get_running_loop().time() + 1.0
        while not handed_over:
            if asyncio.get_running_loop().time() > deadline:
                # The likeliest cause is that `execute_write` already died
                # BEFORE reaching put_nowait. Re-raise that instead of reporting
                # the symptom: `task.cancel()` is a no-op on a finished task, so
                # the real exception would otherwise surface only as a stray
                # "Task exception was never retrieved" during GC, and this file's
                # whole complaint is about guards that fail without pointing
                # anywhere.
                if task.done() and not task.cancelled():
                    original = task.exception()
                    if original is not None:
                        raise AssertionError(
                            "execute_write failed before handing its statement "
                            "to the worker queue"
                        ) from original
                task.cancel()
                pytest.fail(
                    "execute_write never handed its statement to the worker "
                    "queue — nothing was ever in flight, so this repro would "
                    "prove nothing"
                )
            await asyncio.sleep(0)
        # Walk away exactly like a portal loop shutting down after its
        # response: asyncio.run() cancels what is left and closes the loop.
        task.cancel()

    try:
        asyncio.run(_a_loop_that_dies_mid_write())
        conn_before = backend._ensure_conn()
        worker = conn_before._thread.name

        # The loop is gone; now let the parked statement complete and be
        # delivered to it. Unfixed, this is where the worker thread dies.
        blocker.rollback()
        blocker.close()

        async def _still_serving() -> None:
            # A dead worker can never answer this; a live one answers at once.
            await asyncio.wait_for(conn_before.execute("SELECT 1"), timeout=5)

        try:
            asyncio.run(_still_serving())
        except asyncio.TimeoutError:  # pragma: no cover — the regression path
            pytest.fail(
                "the aiosqlite worker thread died delivering a result to a "
                "closed loop; its sqlite3 connection can never be closed now, "
                "and holds its lock for the life of the process"
            )

        assert worker in _live_threads()

        # The payoff: the connection is still RECLAIMABLE. That is asserted at
        # the level this test can speak to — the sqlite3 handle is released and
        # the worker is gone — not by racing a second connection for the write
        # lock. Measured 2026-08-17: a sibling connection in the SAME process
        # can still see "database is locked" here even once this one is fully
        # closed (a fresh process writes the same file immediately), so that
        # would be an assertion about SQLite's in-process lock bookkeeping
        # rather than about the fix. The end-to-end evidence that writes stop
        # blocking is the suite's own wall clock: 38 minutes to 1.6.
        asyncio.run(backend.close())
        # The worker signals the STOP future before it breaks out of its loop,
        # so the awaiting coroutine can resume a beat ahead of the thread
        # actually terminating. Join rather than race it.
        conn_before._thread.join(timeout=5)
        assert worker not in _live_threads()
        assert conn_before._connection is None, (
            "close() returned but the sqlite3 connection is still open"
        )
    finally:
        # Every exit path, including `pytest.fail`, gives the write lock back —
        # a guard that leaks the thing it is testing about is its own hazard.
        blocker.close()


@pytest.mark.asyncio
async def test_evicting_a_closed_loops_client_closes_its_connection(monkeypatch, tmp_path):
    """`db_factory` must release what it evicts.

    Popping the dict entry only makes the client unreachable; the connection,
    its file handle and its lock stay exactly where they were. The registry is
    the last reference, so whatever it drops without closing is orphaned by
    definition.
    """
    from narranexus.platform.settings import settings

    monkeypatch.setattr(
        settings, "database_url", f"sqlite:///{tmp_path / 'evict.db'}"
    )
    monkeypatch.delenv("SQLITE_PROXY_URL", raising=False)

    holder: dict = {}

    async def _acquire_then_die() -> None:
        holder["client"] = await db_factory.get_db_client()
        holder["loop_id"] = id(asyncio.get_running_loop())

    # A whole loop's lifetime, ending with the client still registered.
    thread = threading.Thread(target=lambda: asyncio.run(_acquire_then_die()))
    thread.start()
    thread.join()

    client = holder["client"]
    worker_thread = client._backend._ensure_conn()._thread
    worker = worker_thread.name
    assert holder["loop_id"] in db_factory._clients_by_loop
    assert worker in _live_threads()

    # Any acquisition on a live loop sweeps the dead one.
    mine = await db_factory.get_db_client()

    assert holder["loop_id"] not in db_factory._clients_by_loop
    worker_thread.join(timeout=5)  # close() resolves a beat before the thread ends
    assert worker not in _live_threads(), (
        "the evicted client's connection is still open — db_factory forgot it "
        "instead of closing it"
    )

    # Retire only what this test registered. `close_db_client()` would clear all
    # three process-global registries, including a SYNC_KEY bootstrap client
    # another module may still be holding — a suite-wide side effect from one
    # test, and the kind of thing that only shows up once the order changes.
    await mine.close()
    db_factory._clients_by_loop.pop(id(asyncio.get_running_loop()), None)
    db_factory._locks_by_loop.pop(id(asyncio.get_running_loop()), None)
    db_factory._loops_by_id.pop(id(asyncio.get_running_loop()), None)
