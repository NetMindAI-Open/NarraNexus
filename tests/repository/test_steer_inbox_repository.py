"""
@file_name: test_steer_inbox_repository.py
@author: Bin Liang
@date: 2026-08-21
@description: SteerInboxRepository — the store for live-steering injections
destined for a running turn (keyed by an opaque run_id the orchestrator
assigns). Covers idempotent append (dedup by run_id+msg_id), the unique
index itself, per-run unconsumed FIFO pull, consume-up-to, the write-edge
bounds (width / content-size / backlog back-pressure), and retention.
"""

from datetime import timedelta

import pytest

from narranexus.platform.repository import steer_inbox_repository as sir
from narranexus.platform.repository.steer_inbox_repository import (
    MAX_CONTENT_BYTES,
    SteerInboxFull,
    SteerInboxRepository,
)
from narranexus.platform.schema.steer_schema import SteerInjection
from narranexus.platform.utils.db.dialect_errors import is_unique_violation
from narranexus.platform.utils.timezone import to_datetime6_literal, utc_now


def _inj(run_id: str, msg_id: str, content: str = "x", source: str = "team") -> SteerInjection:
    return SteerInjection(
        run_id=run_id,
        msg_id=msg_id,
        role="user",
        content=content,
        sender_id="agent_sender",
        source=source,
    )


@pytest.mark.asyncio
async def test_append_then_pull_unconsumed_returns_it(db_client):
    repo = SteerInboxRepository(db_client)

    assert await repo.append(_inj("run1", "m1", content="hello")) is True

    pending = await repo.pull_unconsumed("run1")
    assert [p.content for p in pending] == ["hello"]
    assert pending[0].msg_id == "m1"
    assert pending[0].id is not None


@pytest.mark.asyncio
async def test_append_is_idempotent_per_run_and_msg(db_client):
    repo = SteerInboxRepository(db_client)

    assert await repo.append(_inj("run1", "m1")) is True
    # Same (run_id, msg_id) — a re-delivered message must not double-inject.
    assert await repo.append(_inj("run1", "m1", content="changed")) is False

    pending = await repo.pull_unconsumed("run1")
    assert len(pending) == 1  # no duplicate row


@pytest.mark.asyncio
async def test_unique_index_rejects_a_duplicate_direct_insert(db_client):
    # append relies on the (run_id, msg_id) unique index for its dedup: it
    # inserts and treats a unique violation as the duplicate. This guards the
    # index itself — inserting straight past the repository (as a lost race
    # would) must be rejected by the DB. Drop the index from schema_registry
    # and this goes green-to-red.
    row = {
        "run_id": "run1", "msg_id": "m1", "role": "user",
        "content": "x", "sender_id": "agent_sender", "source": "team",
    }
    await db_client.insert("steer_inbox", row)
    with pytest.raises(Exception) as exc_info:
        await db_client.insert("steer_inbox", dict(row))
    assert is_unique_violation(exc_info.value)


@pytest.mark.asyncio
async def test_pull_is_scoped_to_run_and_ordered_by_arrival(db_client):
    repo = SteerInboxRepository(db_client)
    await repo.append(_inj("run1", "m1", content="first"))
    await repo.append(_inj("run2", "other", content="other-run"))
    await repo.append(_inj("run1", "m2", content="second"))

    pending = await repo.pull_unconsumed("run1")
    assert [p.content for p in pending] == ["first", "second"]  # run2 excluded, FIFO


@pytest.mark.asyncio
async def test_mark_consumed_by_msg_ids_consumes_exactly_the_named_rows(db_client):
    # The consumer reports the EXACT msg_ids it drained (not a row-id ceiling),
    # so the loop needs no row id threaded back through the transport.
    repo = SteerInboxRepository(db_client)
    await repo.append(_inj("run1", "m1"))
    await repo.append(_inj("run1", "m2"))
    await repo.append(_inj("run1", "m3"))
    await repo.append(_inj("run2", "m1"))  # another run — must not be touched

    n = await repo.mark_consumed_by_msg_ids("run1", ["m1", "m3"])
    assert n == 2

    assert [p.msg_id for p in await repo.pull_unconsumed("run1")] == ["m2"]
    # run2's identically-named row is untouched (scoped by run_id).
    assert [p.msg_id for p in await repo.pull_unconsumed("run2")] == ["m1"]

    # Re-consuming is a no-op (consumed_at IS NULL guard); empty ids too.
    assert await repo.mark_consumed_by_msg_ids("run1", ["m1"]) == 0
    assert await repo.mark_consumed_by_msg_ids("run1", []) == 0


@pytest.mark.asyncio
async def test_discard_run_deletes_only_this_runs_unconsumed_rows(db_client):
    # At run teardown, rows pushed but never drained are reclaimed by DELETE (not
    # mark-consumed) so the table does not grow unbounded; consumed rows and other
    # runs are untouched.
    repo = SteerInboxRepository(db_client)
    await repo.append(_inj("run1", "m1"))
    await repo.append(_inj("run1", "m2"))
    await repo.append(_inj("run2", "m1"))
    await repo.mark_consumed_by_msg_ids("run1", ["m1"])  # m1 consumed, m2 orphan

    n = await repo.discard_run("run1")
    assert n == 1  # only the un-consumed m2

    # m1 (consumed) survives — it is real audit ("this reached a model"); m2 gone.
    all_run1 = await db_client.execute(
        "SELECT msg_id FROM steer_inbox WHERE run_id = %s", params=("run1",), fetch=True,
    )
    assert [r["msg_id"] for r in (all_run1 or [])] == ["m1"]
    # run2 untouched
    assert len(await repo.pull_unconsumed("run2")) == 1
    # empty / unknown run is a no-op
    assert await repo.discard_run("run_none") == 0


@pytest.mark.asyncio
async def test_mark_consumed_up_to_hides_only_those_at_or_below(db_client):
    repo = SteerInboxRepository(db_client)
    await repo.append(_inj("run1", "m1"))
    await repo.append(_inj("run1", "m2"))
    await repo.append(_inj("run1", "m3"))
    pending = await repo.pull_unconsumed("run1")
    assert len(pending) == 3
    cutoff = pending[1].id  # consume up to and including the second

    consumed = await repo.mark_consumed("run1", cutoff)
    assert consumed == 2

    remaining = await repo.pull_unconsumed("run1")
    assert [p.msg_id for p in remaining] == ["m3"]

    # consumed_at is written in the same format as created_at and reads back
    # as a usable, not-earlier-than-created instant (guards Minor 1: a raw
    # param bypasses the dict serializer, so the format must be set by hand).
    rows = await db_client.execute(
        "SELECT created_at, consumed_at FROM steer_inbox WHERE run_id = %s AND id = %s",
        params=("run1", cutoff), fetch=True,
    )
    row = SteerInjection(run_id="run1", msg_id="m", content="x",
                         sender_id="s", source="team", **rows[0])
    assert row.consumed_at is not None
    assert row.consumed_at >= row.created_at


@pytest.mark.asyncio
async def test_mark_consumed_is_scoped_to_the_run(db_client):
    repo = SteerInboxRepository(db_client)
    await repo.append(_inj("run1", "m1"))
    await repo.append(_inj("run2", "m1"))
    p1 = await repo.pull_unconsumed("run1")

    await repo.mark_consumed("run1", p1[0].id)

    # run2's row is untouched by consuming run1.
    assert len(await repo.pull_unconsumed("run2")) == 1


@pytest.mark.asyncio
async def test_append_rejects_an_over_width_identity_column(db_client):
    # msg_id is VARCHAR(64): SQLite would accept any length silently, MySQL
    # 1406s. The write edge rejects (never clips — clipping two ids to one
    # breaks dedup) so both dialects behave the same.
    repo = SteerInboxRepository(db_client)
    with pytest.raises(ValueError):
        await repo.append(_inj("run1", "m" * 65))


@pytest.mark.asyncio
async def test_append_rejects_content_over_the_size_cap(db_client):
    repo = SteerInboxRepository(db_client)
    with pytest.raises(ValueError):
        await repo.append(_inj("run1", "m1", content="x" * (MAX_CONTENT_BYTES + 1)))


@pytest.mark.asyncio
async def test_append_back_pressures_when_the_run_backlog_is_full(db_client, monkeypatch):
    # Over the cap, the write edge raises so the producer backs off — it never
    # drops a queued message to make room (iron rule #16).
    monkeypatch.setattr(sir, "MAX_UNCONSUMED_PER_RUN", 2)
    repo = SteerInboxRepository(db_client)
    assert await repo.append(_inj("run1", "m1")) is True
    assert await repo.append(_inj("run1", "m2")) is True
    with pytest.raises(SteerInboxFull):
        await repo.append(_inj("run1", "m3"))
    # a DIFFERENT run is unaffected by run1's backlog
    assert await repo.append(_inj("run2", "m1")) is True


@pytest.mark.asyncio
async def test_cleanup_deletes_old_consumed_and_old_unconsumed_orphans_but_keeps_recent(db_client):
    repo = SteerInboxRepository(db_client)
    old = to_datetime6_literal(utc_now() - timedelta(days=30))
    recent_unconsumed = to_datetime6_literal(utc_now() - timedelta(days=2))
    now = to_datetime6_literal(utc_now())
    base = {"run_id": "run1", "role": "user", "sender_id": "s", "source": "team"}
    # old + consumed → deleted (retention arm);
    # old + UNconsumed → deleted (orphan arm: a 30d-old unconsumed row is a dead
    #   run — the teardown discard's structural backstop);
    # recent (2d) + UNconsumed → KEPT (younger than orphan_days=7, so it could
    #   still belong to a live, actively-draining run — never delete un-injected);
    # recent + consumed → kept (younger than the retention cutoff).
    await db_client.insert("steer_inbox", {**base, "msg_id": "old_done", "content": "a", "created_at": old, "consumed_at": old})
    await db_client.insert("steer_inbox", {**base, "msg_id": "old_orphan", "content": "b", "created_at": old})
    await db_client.insert("steer_inbox", {**base, "msg_id": "recent_pending", "content": "e", "created_at": recent_unconsumed})
    await db_client.insert("steer_inbox", {**base, "msg_id": "new_done", "content": "c", "created_at": now, "consumed_at": now})
    # A separate run whose row has a REAL DB-default created_at (second
    # granularity, no microseconds), consumed and recent — confirms the DELETE
    # compares that default format against the microsecond cutoff and keeps it.
    await repo.append(_inj("run_real", "real_new", content="d"))
    real = await repo.pull_unconsumed("run_real")
    await repo.mark_consumed("run_real", real[-1].id)

    deleted = await repo.cleanup_older_than_days(7, 7)
    assert deleted == 2  # old_done (consumed) + old_orphan (unconsumed)

    survivors = await db_client.execute(
        "SELECT run_id, msg_id FROM steer_inbox ORDER BY msg_id",
        fetch=True,
    )
    assert sorted(r["msg_id"] for r in survivors) == ["new_done", "real_new", "recent_pending"]
