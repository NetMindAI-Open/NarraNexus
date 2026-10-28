"""
@file_name: test_transaction_connection_isolation.py
@author: Bin Liang
@date: 2026-08-17
@description: Regression tests for the 2026-08-17 prod outage — every backend
query failing with `pymysql.err.InterfaceError: (0, 'Not connected')` for 19
minutes while RDS itself was healthy, recoverable only by restarting the
container.

Two defects combined:

1. The open transaction's connection lived in an *instance* attribute. One
   `MySQLBackend` serves the whole event loop, so while any caller sat inside
   `transaction()`, every other concurrent caller's statement was routed onto
   that same connection. Two coroutines reading one socket produced
   "readexactly() called while another coroutine is already waiting for
   incoming data" and desynced the MySQL protocol stream.

2. `commit()` cleared the attribute only *after* a successful COMMIT. Once the
   connection died, the attribute kept pointing at it, so every later statement
   in the process was routed onto a dead socket — permanently, with no path
   back except a restart.

The tests below drive the real `MySQLBackend` / `AsyncDatabaseClient` against a
fake pool, so they exercise the actual branch selection rather than a
reimplementation of it.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend


# --------------------------------------------------------------------------
# Fake aiomysql pool / connection
# --------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn
        self.rowcount = 0
        self.lastrowid = 0

    async def execute(self, query, params=None):
        if self._conn.closed:
            raise RuntimeError("(0, 'Not connected')")
        self._conn.queries.append(query)
        self.rowcount = 1
        self.lastrowid = 1

    async def fetchall(self):
        return [{"1": 1}]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    """Mimics the slice of `aiomysql.Connection` the backend touches."""

    def __init__(self, cid: int) -> None:
        self.cid = cid
        self.closed = False
        self.queries: list[str] = []
        self.began = 0
        self.committed = 0
        self.rolled_back = 0
        # Injectable failures
        self.fail_begin = False
        self.fail_commit = False
        self.fail_rollback = False

    def cursor(self, cursor_class=None):
        return FakeCursor(self)

    async def begin(self):
        if self.fail_begin:
            raise RuntimeError("BEGIN failed")
        self.began += 1

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("(0, 'Not connected')")
        self.committed += 1

    async def rollback(self):
        if self.fail_rollback:
            raise RuntimeError("(0, 'Not connected')")
        self.rolled_back += 1

    def close(self):
        self.closed = True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FakeConnection {self.cid} closed={self.closed}>"


class _Acquire:
    """`aiomysql.Pool.acquire()` supports BOTH `await pool.acquire()` and
    `async with pool.acquire()`. The backend uses each form in a different
    place, so the fake has to support both or the test would only cover one."""

    def __init__(self, pool: "FakePool") -> None:
        self._pool = pool
        self._conn: FakeConnection | None = None

    def __await__(self):
        return self._pool._checkout().__await__()

    async def __aenter__(self):
        self._conn = await self._pool._checkout()
        return self._conn

    async def __aexit__(self, *exc):
        assert self._conn is not None
        self._pool.release(self._conn)
        return False


class FakePool:
    def __init__(self) -> None:
        self.created: list[FakeConnection] = []
        self.free: list[FakeConnection] = []
        self.used: list[FakeConnection] = []
        self.discarded: list[FakeConnection] = []
        self.wakeups = 0
        self.closing = False
        self.terminated = False

    async def _checkout(self) -> FakeConnection:
        if self.free:
            conn = self.free.pop()
        else:
            conn = FakeConnection(len(self.created))
            self.created.append(conn)
        self.used.append(conn)
        return conn

    def acquire(self) -> _Acquire:
        return _Acquire(self)

    def release(self, conn: FakeConnection) -> None:
        # aiomysql asserts the connection is currently checked out, and drops a
        # closed connection instead of putting it back on the free list.
        assert conn in self.used, f"double release of {conn}"
        self.used.remove(conn)
        if conn.closed:
            self.discarded.append(conn)
            # Modelled faithfully: aiomysql 0.3.2 schedules its own _wakeup()
            # only for connections that are still open, so a closed one frees
            # the slot WITHOUT notifying anyone waiting in acquire(). The
            # backend compensates by calling _wakeup() itself; this counter is
            # what proves it does.
        else:
            self.free.append(conn)
            self.wakeups += 1

    async def _wakeup(self):
        self.wakeups += 1

    def close(self):
        self.closing = True

    def terminate(self):
        self.terminated = True
        for conn in list(self.used):
            conn.close()
        self.used.clear()

    async def wait_closed(self):
        # aiomysql waits until every borrowed connection is returned — forever
        # if one never is. The fake reproduces that so the shutdown timeout is
        # exercised rather than assumed.
        while self.used and not self.terminated:
            await asyncio.sleep(0.01)


def checked_out(pool: FakePool) -> FakeConnection:
    """The connection the backend just took from the pool.

    Deliberately derived from pool bookkeeping rather than from the backend's
    private transaction attribute: a test that names the new attribute would
    fail against the old code with `AttributeError` and prove nothing about
    behaviour. These tests must fail on the old code for the RIGHT reason.
    """
    assert pool.used, "no connection is checked out"
    return pool.used[-1]


def make_backend() -> tuple[MySQLBackend, FakePool]:
    backend = MySQLBackend({"host": "h", "user": "u", "password": "p", "database": "d"})
    pool = FakePool()
    backend._pool = pool  # skip initialize(); we are not talking to a real server
    return backend, pool


# --------------------------------------------------------------------------
# 1. Blast radius — the actual outage
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_transaction_does_not_capture_other_tasks_statements():
    """The outage in one test.

    While task A holds a transaction, task B issues an ordinary statement. B
    must take its own connection from the pool. Before the fix B's statement
    landed on A's transaction connection, which is what put two coroutines on
    one socket and killed it.
    """
    backend, pool = make_backend()

    inside_transaction = asyncio.Event()
    other_task_done = asyncio.Event()

    async def holder():
        await backend.begin_transaction()
        inside_transaction.set()
        # Hold the transaction open across an await, exactly as a multi-delete
        # wipe does for hundreds of round-trips.
        await other_task_done.wait()
        await backend.execute_write("DELETE FROM narratives WHERE id = 1")
        await backend.commit()

    async def other():
        await inside_transaction.wait()
        await backend.execute("SELECT 1")
        other_task_done.set()

    await asyncio.gather(holder(), other())

    carried_delete = [c for c in pool.created if "DELETE FROM narratives WHERE id = 1" in c.queries]
    carried_select = [c for c in pool.created if "SELECT 1" in c.queries]

    assert len(carried_delete) == 1
    assert len(carried_select) == 1
    assert carried_delete[0] is not carried_select[0], (
        "the concurrent SELECT ran on the open transaction's connection — this is "
        "the two-coroutines-one-socket condition that killed prod on 2026-08-17"
    )


@pytest.mark.asyncio
async def test_statements_in_the_transaction_task_do_use_the_transaction():
    """The isolation must not go so far that the transaction stops working:
    statements issued by the *owning* task still belong to the transaction."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    txn_conn = checked_out(pool)
    await backend.execute("SELECT 1")
    await backend.execute_write("DELETE FROM events WHERE id = 2")
    await backend.commit()

    assert txn_conn.queries == ["SELECT 1", "DELETE FROM events WHERE id = 2"]
    assert txn_conn.committed == 1


# --------------------------------------------------------------------------
# 2. Permanence — why a restart was the only cure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_commit_does_not_poison_later_statements():
    """After a COMMIT failure the next statement must take a healthy pooled
    connection. Before the fix it was routed onto the dead one forever."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    dead = checked_out(pool)
    dead.fail_commit = True

    with pytest.raises(RuntimeError, match="Not connected"):
        await backend.commit()

    rows = await backend.execute("SELECT 1")
    assert rows == [{"1": 1}]
    assert dead.queries == [], "the statement was routed onto the dead connection"


@pytest.mark.asyncio
async def test_failed_commit_closes_the_connection_instead_of_recycling_it():
    """A connection whose COMMIT raised may have a desynced protocol stream.
    Returning it to the free list would hand the corruption to the next
    caller, so it must be closed first — aiomysql then drops it."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    dead = checked_out(pool)
    dead.fail_commit = True

    with pytest.raises(RuntimeError):
        await backend.commit()

    assert dead.closed is True
    assert dead in pool.discarded
    assert dead not in pool.free


@pytest.mark.asyncio
async def test_failed_rollback_also_clears_the_transaction():
    backend, pool = make_backend()

    await backend.begin_transaction()
    dead = checked_out(pool)
    dead.fail_rollback = True

    with pytest.raises(RuntimeError):
        await backend.rollback()

    assert dead.closed is True
    assert dead not in pool.free
    # And the task is out of the transaction: the next statement is routed to a
    # healthy pooled connection rather than the dead one.
    assert await backend.execute("SELECT 1") == [{"1": 1}]
    assert dead.queries == []


@pytest.mark.asyncio
async def test_failed_begin_returns_the_connection_and_leaves_no_transaction():
    """BEGIN failing means the caller never reaches commit/rollback, so the
    connection has to be handed back here or the pool leaks one per attempt."""
    backend, pool = make_backend()

    async def failing_checkout() -> FakeConnection:
        conn = FakeConnection(99)
        conn.fail_begin = True
        pool.created.append(conn)
        pool.used.append(conn)
        return conn

    pool._checkout = failing_checkout  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="BEGIN failed"):
        await backend.begin_transaction()

    assert pool.used == [], "connection leaked on a failed BEGIN"


@pytest.mark.asyncio
async def test_double_begin_is_still_rejected_within_one_task():
    backend, _ = make_backend()
    await backend.begin_transaction()
    with pytest.raises(RuntimeError, match="Already in a transaction"):
        await backend.begin_transaction()
    await backend.commit()


@pytest.mark.asyncio
async def test_two_tasks_can_hold_independent_transactions():
    """Corollary of task scoping: concurrent transactions no longer collide on
    "Already in a transaction", because neither can see the other's."""
    backend, pool = make_backend()
    # Both transactions must be open at the same time, or the test would pass
    # even with the old instance-level state (sequential begin/commit pairs).
    both_open = asyncio.Barrier(2)

    conns: list[FakeConnection] = []

    async def worker(table: str):
        await backend.begin_transaction()
        conns.append(checked_out(pool))
        await both_open.wait()
        await backend.execute_write(f"DELETE FROM {table}")
        await backend.commit()

    await asyncio.gather(worker("a"), worker("b"))

    assert len(conns) == 2
    assert conns[0] is not conns[1]
    assert sorted(c.queries[0] for c in conns) == ["DELETE FROM a", "DELETE FROM b"]


# --------------------------------------------------------------------------
# 3. AsyncDatabaseClient.transaction() — cancellation
# --------------------------------------------------------------------------


class RecordingBackend:
    """Only the transaction seam of `DatabaseBackend` is needed here.

    `AsyncDatabaseClient` now delegates unconditionally — the second, pool-based
    implementation it used to carry was unreachable and has been deleted, so
    these cases pin the delegation and the context manager's unwinding, not a
    parallel copy of the transaction logic.
    """

    def __init__(self) -> None:
        self.events: list[str] = []

    async def begin_transaction(self):
        self.events.append("begin")

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


@pytest.mark.asyncio
async def test_cancellation_inside_a_transaction_rolls_back():
    """`asyncio.CancelledError` does not inherit from `Exception`, so the old
    `except Exception` skipped the rollback entirely when a client disconnect
    made Starlette cancel the request task — leaving the connection checked out
    and the transaction open on the server."""
    fake = RecordingBackend()
    client = AsyncDatabaseClient(_backend=fake)

    with pytest.raises(asyncio.CancelledError):
        async with client.transaction():
            raise asyncio.CancelledError()

    assert fake.events == ["begin", "rollback"]


@pytest.mark.asyncio
async def test_ordinary_exception_still_rolls_back():
    fake = RecordingBackend()
    client = AsyncDatabaseClient(_backend=fake)

    with pytest.raises(ValueError):
        async with client.transaction():
            raise ValueError("boom")

    assert fake.events == ["begin", "rollback"]


@pytest.mark.asyncio
async def test_success_path_commits_once():
    fake = RecordingBackend()
    client = AsyncDatabaseClient(_backend=fake)

    async with client.transaction():
        pass

    assert fake.events == ["begin", "commit"]


@pytest.mark.asyncio
async def test_failing_rollback_does_not_mask_the_original_error():
    """The caller must still see what actually went wrong; a secondary failure
    while unwinding is logged, not raised."""

    class ExplodingRollback(RecordingBackend):
        async def rollback(self):
            self.events.append("rollback")
            raise RuntimeError("rollback failed too")

    fake = ExplodingRollback()
    client = AsyncDatabaseClient(_backend=fake)

    with pytest.raises(ValueError, match="original"):
        async with client.transaction():
            raise ValueError("original")

    assert fake.events == ["begin", "rollback"]


@pytest.mark.asyncio
async def test_failed_commit_propagates_without_a_second_rollback():
    """A failed COMMIT has already released the connection, so following it
    with a rollback would raise "No active transaction" and hide the real
    error."""

    class FailingCommit(RecordingBackend):
        async def commit(self):
            self.events.append("commit")
            raise RuntimeError("(0, 'Not connected')")

    fake = FailingCommit()
    client = AsyncDatabaseClient(_backend=fake)

    with pytest.raises(RuntimeError, match="Not connected"):
        async with client.transaction():
            pass

    assert fake.events == ["begin", "commit"], "rollback must not run after a failed commit"


# --------------------------------------------------------------------------
# 4. Child tasks — the half of ContextVar semantics that is NOT isolation
# --------------------------------------------------------------------------
#
# A ContextVar value is COPIED into every task created after it was set. So
# "task-scoped" buys isolation from unrelated tasks, but a task spawned INSIDE
# the transaction body inherits the connection. Left unguarded that reproduces
# the original bug at smaller scale — and worse, a child that commits releases
# a connection the parent is still writing to, so the parent's own commit
# double-releases and aiomysql raises AssertionError.


@pytest.mark.asyncio
async def test_child_tasks_do_not_borrow_the_parents_transaction_connection():
    backend, pool = make_backend()

    await backend.begin_transaction()
    parent_conn = checked_out(pool)

    async def child(n: int):
        await backend.execute(f"SELECT child_{n}")

    await asyncio.gather(child(1), child(2))

    assert parent_conn.queries == [], (
        "child tasks put their statements on the parent's transaction "
        "connection — two coroutines, one socket, which is the original bug"
    )
    # They may well reuse the SAME pooled connection one after the other —
    # `pool.acquire()` hands it out exclusively, so sequential reuse is correct.
    # The property under test is only that neither touched the parent's.
    ran = [q for c in pool.created for q in c.queries if q.startswith("SELECT child_")]
    assert sorted(ran) == ["SELECT child_1", "SELECT child_2"]

    await backend.commit()


@pytest.mark.asyncio
async def test_a_child_task_cannot_end_the_parents_transaction():
    """Ending it would release a connection the parent is still using, and the
    parent's own commit would then double-release."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    parent_conn = checked_out(pool)

    async def child():
        with pytest.raises(RuntimeError, match="belongs to the task that opened it"):
            await backend.commit()
        with pytest.raises(RuntimeError, match="belongs to the task that opened it"):
            await backend.rollback()

    await asyncio.create_task(child())

    # The parent's transaction is untouched and its own commit still works.
    await backend.execute_write("DELETE FROM events WHERE id = 1")
    await backend.commit()
    assert parent_conn.committed == 1
    assert parent_conn.queries == ["DELETE FROM events WHERE id = 1"]


@pytest.mark.asyncio
async def test_a_child_task_may_open_its_own_transaction():
    """Inheriting the parent's value must not look like 'already in a
    transaction' — the child is entitled to its own."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    parent_conn = checked_out(pool)

    async def child():
        await backend.begin_transaction()
        child_conn = checked_out(pool)
        assert child_conn is not parent_conn
        await backend.execute_write("DELETE FROM child_rows")
        await backend.commit()
        return child_conn

    child_conn = await asyncio.create_task(child())

    assert child_conn.committed == 1
    assert parent_conn.committed == 0
    await backend.commit()
    assert parent_conn.committed == 1


# --------------------------------------------------------------------------
# 5. Returning a broken connection must not strand queued requests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_releasing_a_broken_connection_wakes_pool_waiters():
    """aiomysql 0.3.2 schedules its wake-up only for connections that are still
    open, so returning a closed one frees the slot without notifying anyone in
    acquire(). With the pool saturated and every commit failing — precisely
    this incident — queued requests would hang instead of failing fast."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    dead = checked_out(pool)
    dead.fail_commit = True

    before = pool.wakeups
    with pytest.raises(RuntimeError):
        await backend.commit()
    await asyncio.sleep(0)  # let the scheduled wake-up run

    assert pool.wakeups > before, "pool waiters were never notified"


# --------------------------------------------------------------------------
# 6. Shutdown must not hang
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_terminates_connections_that_never_come_back():
    """`pool.close()` only stops new checkouts; `wait_closed()` waits for the
    borrowed ones forever. A connection held by another task at shutdown would
    otherwise keep the container alive until docker SIGKILLs it."""
    import narranexus.platform.utils.db.db_backend_mysql as mod

    backend, pool = make_backend()

    # A connection borrowed by someone else and never returned.
    stranded = await pool._checkout()

    original = mod._POOL_CLOSE_TIMEOUT_SEC
    mod._POOL_CLOSE_TIMEOUT_SEC = 0.05
    try:
        await asyncio.wait_for(backend.close(), timeout=2.0)
    finally:
        mod._POOL_CLOSE_TIMEOUT_SEC = original

    assert pool.terminated is True
    assert stranded.closed is True


@pytest.mark.asyncio
async def test_a_child_tasks_write_fails_loudly_instead_of_escaping_the_transaction():
    """A child task cannot join the parent's transaction (that is the
    two-coroutines-one-socket bug). Letting it quietly take a pooled connection
    is worse than refusing: the write autocommits, so the enclosing rollback
    does not undo it, and nobody is told the write left the transaction it
    appeared to be inside."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    parent_conn = checked_out(pool)

    async def child():
        with pytest.raises(RuntimeError, match="inherited an enclosing transaction"):
            await backend.execute_write("DELETE FROM events WHERE id = 1")
        with pytest.raises(RuntimeError, match="inherited an enclosing transaction"):
            await backend.insert("events", {"event_id": "e1"})

    await asyncio.create_task(child())

    assert parent_conn.queries == []
    assert not any(c.queries for c in pool.created if c is not parent_conn), (
        "a rejected write still touched a pooled connection"
    )
    await backend.commit()


@pytest.mark.asyncio
async def test_a_child_tasks_read_is_still_allowed():
    """Reads are the common, harmless case — `gather`-ing queries inside a
    transaction body must keep working. They simply do not see uncommitted
    rows, which is what taking a pooled connection means."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    parent_conn = checked_out(pool)

    async def child():
        return await backend.execute("SELECT 1")

    rows = await asyncio.create_task(child())

    assert rows == [{"1": 1}]
    assert parent_conn.queries == []
    await backend.commit()


@pytest.mark.asyncio
async def test_writes_outside_any_transaction_are_unaffected():
    """The rejection must key on 'inherited a transaction', not on 'is not the
    owner' — with no transaction anywhere, every task writes normally."""
    backend, pool = make_backend()

    async def writer(n: int):
        await backend.execute_write(f"DELETE FROM t WHERE id = {n}")

    await asyncio.gather(writer(1), writer(2))

    written = sorted(q for c in pool.created for q in c.queries)
    assert written == ["DELETE FROM t WHERE id = 1", "DELETE FROM t WHERE id = 2"]


@pytest.mark.asyncio
async def test_the_shutdown_budget_stays_under_the_evict_sweep_budget():
    """`db_factory._evict_closed_loops` wraps `client.close()` in a `wait_for`
    whose timeout is what remains of its own sweep budget. If the inner budget
    is not smaller, the outer timeout always wins: `close()` is cancelled inside
    its own `wait_for`, its `except asyncio.TimeoutError` never runs (a
    cancellation is a BaseException), `terminate()` never fires, and the pool
    leaks in exactly the path the fallback exists for.

    The relationship lives in two files, so it is asserted rather than left to a
    comment — the comment was already there when the two constants were equal.
    """
    from narranexus.platform.utils.db import db_backend_mysql, db_factory

    assert db_backend_mysql._POOL_CLOSE_TIMEOUT_SEC < db_factory._EVICT_SWEEP_BUDGET


@pytest.mark.asyncio
async def test_a_child_task_outliving_the_transaction_can_write_again():
    """The guard needs an expiry, not just a condition.

    A ContextVar copies its VALUE into tasks created after it was set, so an
    immutable `(owner, conn)` tuple gave the child a snapshot that never went
    stale: the parent's clear-on-commit changed only the parent's context, and
    the child stayed locked out forever — refused in the name of a transaction
    that had ended seconds earlier. Sharing one mutable holder makes the end of
    the transaction visible to everyone who inherited it.
    """
    backend, pool = make_backend()

    released = asyncio.Event()
    child_done = asyncio.Event()
    result = {}

    async def child():
        # Created INSIDE the transaction, so it inherits the holder.
        await released.wait()
        try:
            await backend.execute_write("DELETE FROM t WHERE id = 1")
            result["ok"] = True
        except RuntimeError as exc:
            result["ok"] = False
            result["err"] = str(exc)
        child_done.set()

    await backend.begin_transaction()
    task = asyncio.create_task(child())
    await asyncio.sleep(0)          # let the child start and inherit the context
    await backend.commit()

    released.set()
    await child_done.wait()
    await task

    assert result.get("ok") is True, (
        f"child locked out after the transaction ended: {result.get('err')}"
    )


@pytest.mark.asyncio
async def test_a_second_transaction_does_not_resurrect_a_stale_childs_snapshot():
    """`begin_transaction` must allocate a NEW holder. Reusing one would make
    the parent's next transaction revive an old child's expired view — harder to
    diagnose than the bug it replaced."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    stale_child_started = asyncio.Event()
    verdict = {}

    async def child():
        stale_child_started.set()
        await asyncio.sleep(0.02)   # outlive both transactions
        try:
            await backend.execute_write("DELETE FROM t WHERE id = 2")
            verdict["ok"] = True
        except RuntimeError as exc:
            verdict["ok"] = False
            verdict["err"] = str(exc)

    task = asyncio.create_task(child())
    await stale_child_started.wait()
    await backend.commit()

    # A second, unrelated transaction in the parent.
    await backend.begin_transaction()
    await backend.execute_write("DELETE FROM t WHERE id = 3")
    await backend.commit()

    await task
    assert verdict.get("ok") is True, (
        f"the stale child was revived by a later transaction: {verdict.get('err')}"
    )


# --------------------------------------------------------------------------
# 7. The health probe must not poison the pool when it is cancelled
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cancelled_probe_closes_its_connection_instead_of_recycling_it():
    """`/health` bounds the probe with a timeout, so cancellation arrives while
    the driver may be halfway through reading a response. Going through
    `execute` would return that connection to the free list — aiomysql only
    discards ones already closed, and cancellation does not close them.

    The timing is what makes it matter: the timeout fires precisely when the
    database is slow, so the container healthcheck would reproduce this every
    30 seconds for the length of a slowdown, poisoning one more connection each
    time. A probe added to make failure visible must not manufacture it.
    """
    backend, pool = make_backend()

    started = asyncio.Event()

    class HangingCursor(FakeCursor):
        async def execute(self, query, params=None):
            started.set()
            await asyncio.sleep(3600)

    hung = FakeConnection(99)
    hung.cursor = lambda cursor_class=None: HangingCursor(hung)
    pool.created.append(hung)

    async def checkout():
        pool.used.append(hung)
        return hung

    pool._checkout = checkout  # type: ignore[method-assign]

    task = asyncio.create_task(backend.probe())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert hung.closed is True, "a cancelled probe left its connection open"
    assert hung not in pool.free, "the cancelled probe's connection went back on the free list"
    assert hung in pool.discarded


@pytest.mark.asyncio
async def test_a_successful_probe_returns_its_connection_to_the_pool():
    """The other half: a healthy probe must not leak a connection per call."""
    backend, pool = make_backend()

    await backend.probe()

    assert pool.used == [], "the probe kept its connection checked out"
    assert len(pool.free) == 1
    assert pool.free[0].queries == ["SELECT 1"]


@pytest.mark.asyncio
async def test_the_probe_uses_the_ordinary_pool_rather_than_a_private_connection():
    """A probe with its own connection proves nothing about what real requests
    experience — which is how a hardcoded `"database": "connected"` let a total
    outage look healthy for 19 minutes."""
    backend, pool = make_backend()

    await backend.probe()

    assert len(pool.created) == 1, "the probe built a connection outside the pool"


# --------------------------------------------------------------------------
# 8. Lazy initialisation must hand back a backend on EVERY path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lazily_initialised_sqlite_client_can_probe_before_anything_else(tmp_path, monkeypatch):
    """`_ensure_backend` promises a `DatabaseBackend` on every path.

    Three of its four branches (the SQLite ones) used to `return None` — a
    leftover from when the method returned a pool. Nothing caught it because the
    live paths all go through `db_factory`, which pre-seeds `_backend` so the
    first early-return short-circuits. A client built the way the class
    docstring recommends, on the desktop/local SQLite backend, got
    `AttributeError` from `probe()` and a silent `False` from `ping()` against a
    perfectly healthy database.
    """
    from narranexus.platform.settings import settings
    from narranexus.platform.utils.db.database import AsyncDatabaseClient

    db_file = tmp_path / "probe.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_file}", raising=False)

    client = AsyncDatabaseClient()
    await client.probe()            # must not raise
    assert await client.ping() is True

    fresh = AsyncDatabaseClient()
    async with fresh.transaction():  # begin/commit also dereference the return
        pass


# --------------------------------------------------------------------------
# 9. A finished transaction reads as finished, not as someone else's
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_child_committing_after_the_parent_is_told_there_is_no_transaction():
    """Message quality, and the reason `_owned_or_raise` checks `conn is None`
    before it checks ownership. Once the holder is cleared the transaction is
    over for everyone, so "no active transaction" is the truth; reporting
    "it belongs to the task that opened it" would send the reader looking for a
    live transaction that no longer exists."""
    backend, pool = make_backend()

    await backend.begin_transaction()
    ready = asyncio.Event()
    seen = {}

    async def child():
        ready.set()
        await asyncio.sleep(0.02)
        try:
            await backend.commit()
        except RuntimeError as exc:
            seen["msg"] = str(exc)

    task = asyncio.create_task(child())
    await ready.wait()
    await backend.commit()
    await task

    assert seen.get("msg") == "No active transaction", (
        f"a finished transaction reported as someone else's: {seen.get('msg')!r}"
    )


@pytest.mark.asyncio
async def test_the_owner_itself_is_released_by_clearing_the_holder():
    """`_own_txn` checks `txn.conn is None` before it checks ownership, and that
    check has to be load-bearing for the OWNER too, not only for children.

    `_clear_txn` blanks the shared holder and then clears this task's own
    ContextVar. If it ever stops doing the second half, the owner would still be
    routed by the first — so this pins that a committed owner goes back to the
    pool like anyone else.
    """
    backend, pool = make_backend()

    await backend.begin_transaction()
    txn_conn = checked_out(pool)
    await backend.commit()

    await backend.execute_write("DELETE FROM t WHERE id = 7")

    # It may well land on the very same connection — commit put it back on the
    # free list and `pool.acquire()` hands it out again. What must be true is
    # that it went through the POOL: acquired and released, not held as an open
    # transaction.
    assert pool.used == [], "the write was routed onto a still-open transaction"
    assert len(pool.free) == 1
    assert "DELETE FROM t WHERE id = 7" in pool.free[0].queries
    assert txn_conn.committed == 1


@pytest.mark.asyncio
async def test_every_crud_method_works_on_a_lazily_initialised_client(tmp_path, monkeypatch):
    """The FIRST call on a client that has not resolved its backend yet.

    `insert` / `update` / `delete` / `upsert` used to carry a tail left over
    from the deleted pool path that referenced an undefined `fetch`, so the
    first call on such a client raised `NameError: name 'fetch' is not defined`
    — an error with no relationship to what the caller was doing. It survived
    because every production caller goes through `db_factory`, which pre-seeds
    the backend so the fast path short-circuits, and because `F821` (undefined
    name) is in this repo's global ruff ignore list.

    The previous round's lazy-path test stopped one method short of all four.
    This one starts a NEW client per method so each really takes the cold path.
    """
    from narranexus.platform.settings import settings
    from narranexus.platform.utils.db.database import AsyncDatabaseClient

    db_file = tmp_path / "crud.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_file}", raising=False)

    await AsyncDatabaseClient().execute(
        "CREATE TABLE IF NOT EXISTS t (k VARCHAR(32) PRIMARY KEY, v VARCHAR(32))",
        fetch=False,
    )

    # Each on a cold client — the branch that used to raise NameError.
    await AsyncDatabaseClient().insert("t", {"k": "a", "v": "1"})
    await AsyncDatabaseClient().update("t", {"k": "a"}, {"v": "2"})
    await AsyncDatabaseClient().upsert("t", {"k": "b", "v": "3"}, "k")
    rows = await AsyncDatabaseClient().get("t", {})
    assert {r["k"]: r["v"] for r in rows} == {"a": "2", "b": "3"}

    assert await AsyncDatabaseClient().get_one("t", {"k": "a"}) == {"k": "a", "v": "2"}
    assert [r["k"] for r in await AsyncDatabaseClient().get_by_ids("t", "k", ["b", "a"])] == ["b", "a"]

    await AsyncDatabaseClient().delete("t", {"k": "a"})
    assert await AsyncDatabaseClient().get_one("t", {"k": "a"}) is None


@pytest.mark.asyncio
async def test_the_empty_input_guards_survived_the_collapse(tmp_path, monkeypatch):
    """Collapsing the two paths into one must not drop the validation that only
    lived on the deleted half."""
    from narranexus.platform.settings import settings
    from narranexus.platform.utils.db.database import AsyncDatabaseClient

    monkeypatch.setattr(
        settings, "database_url", f"sqlite:///{tmp_path / 'guards.db'}", raising=False
    )
    db = AsyncDatabaseClient()

    # Anchored: "Insert data cannot be empty" is a prefix of the None-filtered
    # message too, so a loose match would pass even if the two collapsed into one.
    with pytest.raises(ValueError, match=r"^Insert data cannot be empty$"):
        await db.insert("t", {})
    with pytest.raises(ValueError, match="no valid fields after filtering"):
        await db.insert("t", {"k": None})
    with pytest.raises(ValueError, match="Update data cannot be empty"):
        await db.update("t", {"k": "a"}, {})
    with pytest.raises(ValueError, match="must specify filter conditions"):
        await db.update("t", {}, {"v": "1"})
    with pytest.raises(ValueError, match="must specify filter conditions"):
        await db.delete("t", {})
    with pytest.raises(ValueError, match="Insert data cannot be empty"):
        await db.upsert("t", {}, "k")
