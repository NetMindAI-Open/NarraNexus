"""
@file_name: test_steer_inbox_mysql.py
@author: Bin Liang
@date: 2026-08-21
@description: Real-MySQL dialect twin for SteerInboxRepository's hand-written
SQL (iron rule: raw SQL gets a MySQL twin, not just the SQLite suite).

The SQLite suite proves nothing about the only dialect dev/prod run — and
the append pre-check + unique-index dedup, the `consumed_at IS NULL` pull,
and the `id <= cursor` scoped consume are exactly the shapes a dialect gap
would hide. Enable with NARRANEXUS_MYSQL_TEST_URL; skipped otherwise.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio

from narranexus.platform.repository.steer_inbox_repository import SteerInboxRepository
from narranexus.platform.schema.steer_schema import SteerInjection
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from narranexus.platform.utils.timezone import to_datetime6_literal, utc_now
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that steer_inbox's append dedup (unique index), the consumed_at "
        "IS NULL pull and the id <= cursor scoped consume parse and behave "
        "on the real MySQL dialect"
    ),
)

RUN_A = "run_mysql_twin_a"
RUN_B = "run_mysql_twin_b"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        for run in (RUN_A, RUN_B):
            await client.execute(
                "DELETE FROM steer_inbox WHERE run_id = %s", params=(run,),
                fetch=False,  # a DELETE belongs on the write path, not the read path
            )

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


def _inj(run_id: str, msg_id: str, content: str = "x") -> SteerInjection:
    return SteerInjection(
        run_id=run_id, msg_id=msg_id, role="user", content=content,
        sender_id="agent_sender", source="team",
    )


@pytest.mark.asyncio
async def test_append_dedup_pull_and_scoped_consume_roundtrip(mysql_client):
    repo = SteerInboxRepository(mysql_client)

    assert await repo.append(_inj(RUN_A, "m1", "first")) is True
    assert await repo.append(_inj(RUN_A, "m1", "dup")) is False  # unique index
    assert await repo.append(_inj(RUN_A, "m2", "second")) is True
    assert await repo.append(_inj(RUN_B, "m1", "other-run")) is True

    pending = await repo.pull_unconsumed(RUN_A)
    assert [p.content for p in pending] == ["first", "second"]  # FIFO, run-scoped

    consumed = await repo.mark_consumed(RUN_A, pending[0].id)
    assert consumed == 1
    assert [p.msg_id for p in await repo.pull_unconsumed(RUN_A)] == ["m2"]

    # consuming RUN_A left RUN_B untouched
    assert len(await repo.pull_unconsumed(RUN_B)) == 1


@pytest.mark.asyncio
async def test_mark_consumed_by_msg_ids_in_clause_on_mysql(mysql_client):
    # The new `msg_id IN (%s, %s, …)` UPDATE — a real MySQL dialect check on the
    # variadic placeholder list; SQLite alone cannot see a dialect error here.
    repo = SteerInboxRepository(mysql_client)
    await repo.append(_inj(RUN_A, "x1", "a"))
    await repo.append(_inj(RUN_A, "x2", "b"))
    await repo.append(_inj(RUN_A, "x3", "c"))
    await repo.append(_inj(RUN_B, "x1", "other"))

    n = await repo.mark_consumed_by_msg_ids(RUN_A, ["x1", "x3"])
    assert n == 2
    assert [p.msg_id for p in await repo.pull_unconsumed(RUN_A)] == ["x2"]
    assert len(await repo.pull_unconsumed(RUN_B)) == 1  # run-scoped
    assert await repo.mark_consumed_by_msg_ids(RUN_A, []) == 0  # empty is a no-op


@pytest.mark.asyncio
async def test_discard_run_delete_on_mysql(mysql_client):
    # discard_run's DELETE … WHERE run_id = %s AND consumed_at IS NULL on a real
    # MySQL dialect.
    repo = SteerInboxRepository(mysql_client)
    await repo.append(_inj(RUN_A, "d1"))
    await repo.append(_inj(RUN_A, "d2"))
    await repo.append(_inj(RUN_B, "d1"))
    await repo.mark_consumed_by_msg_ids(RUN_A, ["d1"])  # d1 consumed, d2 orphan

    assert await repo.discard_run(RUN_A) == 1  # only un-consumed d2
    assert await repo.pull_unconsumed(RUN_A) == []
    assert len(await repo.pull_unconsumed(RUN_B)) == 1  # run-scoped


@pytest.mark.asyncio
async def test_backlog_count_and_retention_delete_on_mysql(mysql_client, monkeypatch):
    # The COUNT-unconsumed backlog guard and the retention DELETE are both raw
    # SQL — exercise them on the real dialect (IS NULL / IS NOT NULL / < cutoff).
    import narranexus.platform.repository.steer_inbox_repository as sir

    repo = SteerInboxRepository(mysql_client)
    monkeypatch.setattr(sir, "MAX_UNCONSUMED_PER_RUN", 2)
    assert await repo.append(_inj(RUN_A, "m1")) is True
    assert await repo.append(_inj(RUN_A, "m2")) is True
    with pytest.raises(sir.SteerInboxFull):
        await repo.append(_inj(RUN_A, "m3"))

    old = to_datetime6_literal(utc_now() - timedelta(days=30))
    recent = to_datetime6_literal(utc_now() - timedelta(days=2))
    base = {"run_id": RUN_B, "role": "user", "sender_id": "s", "source": "team"}
    await mysql_client.insert("steer_inbox", {**base, "msg_id": "old_done", "content": "a", "created_at": old, "consumed_at": old})
    await mysql_client.insert("steer_inbox", {**base, "msg_id": "old_orphan", "content": "b", "created_at": old})
    await mysql_client.insert("steer_inbox", {**base, "msg_id": "recent_pending", "content": "e", "created_at": recent})
    # Retention arm deletes old_done; orphan arm deletes the 30d-old unconsumed
    # old_orphan; the 2d-old recent_pending is kept (younger than orphan_days=7).
    assert await repo.cleanup_older_than_days(7, 7) == 2
    assert [p.msg_id for p in await repo.pull_unconsumed(RUN_B)] == ["recent_pending"]
