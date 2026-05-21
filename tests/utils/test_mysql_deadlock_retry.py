"""
@file_name: test_mysql_deadlock_retry.py
@author: Bin Liang
@date: 2026-05-19
@description: `MySQLBackend.execute` / `execute_write` must transparently
retry on InnoDB deadlock (errno 1213) when there is no caller-owned
transaction. Inside an explicit transaction, retry is unsafe — the
caller controls the transaction boundary and re-running a single
statement would leave the previous statements un-rolled-back.

Observed on EC2 backend container 2026-05-19T05:19:27:
  pymysql.err.OperationalError: (1213, 'Deadlock found when trying to
  get lock; try restarting transaction')

The deadlock was between `DELETE /api/auth/agents/<id>` (cascading
DELETE across 13 tables, 77 rows) and 4 concurrent agent_run flows
writing event rows. Both are legitimate operations; retry-on-1213 is
the canonical fix.
"""
from __future__ import annotations

import pymysql.err
import pytest

from xyz_agent_context.utils import db_backend_mysql
from xyz_agent_context.utils.db_backend_mysql import _retry_on_deadlock


@pytest.fixture(autouse=True)
def _instant_sleep(monkeypatch):
    """Override `asyncio.sleep` only inside db_backend_mysql so the
    retry's backoff sleep is a noop. We can't patch via dotted path
    `db_backend_mysql.asyncio.sleep` because `asyncio` is a shared
    module — that path resolves to the global `asyncio.sleep` and
    recursing through our lambda would loop forever."""

    class _FakeAsyncio:
        @staticmethod
        async def sleep(_seconds):
            return None

    monkeypatch.setattr(db_backend_mysql, "asyncio", _FakeAsyncio)


@pytest.mark.asyncio
async def test_retries_and_succeeds_after_one_deadlock():
    """First call raises 1213; second call succeeds."""
    calls = []

    async def coro():
        calls.append(None)
        if len(calls) < 2:
            raise pymysql.err.OperationalError(1213, "Deadlock found")
        return "ok"

    result = await _retry_on_deadlock(coro, max_attempts=3)
    assert result == "ok"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts():
    calls = []

    async def coro():
        calls.append(None)
        raise pymysql.err.OperationalError(1213, "Deadlock found")

    with pytest.raises(pymysql.err.OperationalError) as excinfo:
        await _retry_on_deadlock(coro, max_attempts=3)
    assert excinfo.value.args[0] == 1213
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_does_not_retry_non_deadlock_operational_error():
    """errno 2003 (Can't connect) is not a deadlock; bubble it up immediately."""
    calls = []

    async def coro():
        calls.append(None)
        raise pymysql.err.OperationalError(2003, "Can't connect to MySQL server")

    with pytest.raises(pymysql.err.OperationalError) as excinfo:
        await _retry_on_deadlock(coro, max_attempts=3)
    assert excinfo.value.args[0] == 2003
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_does_not_retry_unrelated_exception():
    async def coro():
        raise ValueError("not a db error")

    with pytest.raises(ValueError):
        await _retry_on_deadlock(coro, max_attempts=3)
