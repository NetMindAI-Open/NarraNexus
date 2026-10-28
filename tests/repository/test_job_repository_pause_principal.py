"""
@file_name: test_job_repository_pause_principal.py
@author: Bin Liang
@date: 2026-09-10
@description: `JobRepository.pause_jobs_for_execution_principal` — the single
batched UPDATE behind an admin account suspension (B-13, review I2/I3).

Selects by EXECUTION PRINCIPAL (`related_entity_id`, else `user_id`) — the
same identity JobTrigger judges per job — so the admin half and the poller
half of a suspension can never disagree about a job. One statement, no
500-row fetch ceiling, rowcount returned. The `_mysql` twin runs the same
statement against the real dialect.
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import JobStatus

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'
BANNED = "u_banned"
OTHER = "u_other"


async def _seed_job(db, job_id, user_id, *, status="active", related_entity_id=None,
                    paused_reason=None, paused_at=None):
    row = {
        "job_id": job_id, "instance_id": f"ins_{job_id}", "agent_id": "agent_1",
        "user_id": user_id, "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": status, "notification_method": "inbox",
    }
    if related_entity_id is not None:
        row["related_entity_id"] = related_entity_id
    if paused_reason is not None:
        row["paused_reason"] = paused_reason
    if paused_at is not None:
        row["paused_at"] = paused_at
    await db.insert("instance_jobs", row)


async def _status(db, job_id):
    row = await db.get_one("instance_jobs", {"job_id": job_id})
    return row["status"], row["paused_reason"]


@pytest.mark.asyncio
async def test_pauses_jobs_owned_and_run_as_the_principal(db_client):
    await _seed_job(db_client, "own_active", BANNED, status="active")
    await _seed_job(db_client, "own_pending", BANNED, status="pending")
    await _seed_job(db_client, "own_empty_rel", BANNED, status="active", related_entity_id="")

    paused = await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned")

    assert paused == 3
    for job_id in ("own_active", "own_pending", "own_empty_rel"):
        assert await _status(db_client, job_id) == ("paused", "banned")


@pytest.mark.asyncio
async def test_pauses_a_job_owned_by_someone_else_but_executed_as_the_principal(db_client):
    await _seed_job(db_client, "delegated", OTHER, status="active", related_entity_id=BANNED)

    paused = await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned")

    assert paused == 1
    assert await _status(db_client, "delegated") == ("paused", "banned")


@pytest.mark.asyncio
async def test_leaves_a_job_owned_by_the_principal_but_executed_as_someone_else(db_client):
    """The poller would run this job as OTHER, who may transact — pausing it
    here would contradict the poller's judgement (the I2 disagreement)."""
    await _seed_job(db_client, "runs_as_other", BANNED, status="active", related_entity_id=OTHER)

    paused = await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned")

    assert paused == 0
    assert await _status(db_client, "runs_as_other") == ("active", None)


@pytest.mark.asyncio
async def test_leaves_other_users_jobs_alone(db_client):
    await _seed_job(db_client, "other_active", OTHER, status="active")

    assert await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned") == 0
    assert await _status(db_client, "other_active") == ("active", None)


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed"])
@pytest.mark.asyncio
async def test_terminal_jobs_are_not_rewritten(db_client, terminal):
    await _seed_job(db_client, f"job_{terminal}", BANNED, status=terminal)

    assert await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned") == 0
    assert (await _status(db_client, f"job_{terminal}"))[0] == terminal


@pytest.mark.asyncio
async def test_cooling_jobs_are_paused(db_client):
    """COOLING is re-armed to ACTIVE by the clock — it would start on its own."""
    await _seed_job(db_client, "cooling", BANNED, status="cooling")

    assert await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned") == 1
    assert await _status(db_client, "cooling") == ("paused", "banned")


@pytest.mark.parametrize(
    "untouched",
    ["blocked", "blocked_failed", "running", "paused_no_quota", "paused_spend_cap"],
)
@pytest.mark.asyncio
async def test_non_schedulable_statuses_are_left_as_they_are(db_client, untouched):
    """Review I1: reinstate turns every suspension-paused job ACTIVE. A BLOCKED
    / BLOCKED_FAILED job flattened to paused would come back ACTIVE and run
    before its dependency output exists; RUNNING belongs to the in-flight
    run's finalizer; the auto-paused states keep their own reason."""
    await _seed_job(db_client, f"job_{untouched}", BANNED, status=untouched)
    await _seed_job(db_client, "job_active", BANNED, status="active")

    assert await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned") == 1
    assert await _status(db_client, f"job_{untouched}") == (untouched, None)
    assert await _status(db_client, "job_active") == ("paused", "banned")


@pytest.mark.asyncio
async def test_already_paused_for_this_reason_keeps_its_paused_at(db_client):
    await _seed_job(
        db_client, "already", BANNED, status="paused", paused_reason="banned",
        paused_at="2026-01-01 00:00:00.000000",
    )

    assert await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned") == 0
    row = await db_client.get_one("instance_jobs", {"job_id": "already"})
    assert str(row["paused_at"]).startswith("2026-01-01")


@pytest.mark.asyncio
async def test_already_paused_jobs_keep_their_own_reason(db_client):
    """Review r2 I-B: a job already paused (by the user, or with no recorded
    reason) is NOT relabelled — reinstate resumes only reasons a suspension
    wrote, so relabelling would make reinstate un-pause the user's own pause."""
    await _seed_job(db_client, "user_paused", BANNED, status="paused", paused_reason="user")
    await _seed_job(db_client, "null_reason", BANNED, status="paused")

    assert await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned") == 0
    assert await _status(db_client, "user_paused") == ("paused", "user")
    assert await _status(db_client, "null_reason") == ("paused", None)


@pytest.mark.asyncio
async def test_no_row_ceiling(db_client):
    """The old fetch-then-loop stopped silently at 500 rows."""
    for i in range(520):
        await _seed_job(db_client, f"bulk_{i}", BANNED, status="active")

    assert await JobRepository(db_client).pause_jobs_for_execution_principal(BANNED, "banned") == 520
    rows = await db_client.execute(
        "SELECT COUNT(*) AS n FROM instance_jobs WHERE user_id = %s AND status = %s",
        (BANNED, JobStatus.ACTIVE.value), fetch=True,
    )
    assert rows[0]["n"] == 0


# ── review r2 I-B: the read half of reinstate ─────────────────────────────────

SUSPENSION_REASONS = ("banned", "blocked", "deleted")


async def _ids(db, user_id=BANNED, reasons=SUSPENSION_REASONS):
    jobs = await JobRepository(db).get_jobs_paused_for_execution_principal(user_id, reasons)
    return sorted(j.job_id for j in jobs)


@pytest.mark.asyncio
async def test_reinstate_read_selects_suspension_paused_jobs_by_principal(db_client):
    await _seed_job(db_client, "own", BANNED, status="paused", paused_reason="banned")
    await _seed_job(db_client, "own_empty_rel", BANNED, status="paused", paused_reason="banned",
                    related_entity_id="")
    await _seed_job(db_client, "delegated", OTHER, status="paused", paused_reason="banned",
                    related_entity_id=BANNED)
    await _seed_job(db_client, "gate_blocked", BANNED, status="paused", paused_reason="blocked")

    assert await _ids(db_client) == ["delegated", "gate_blocked", "own", "own_empty_rel"]


@pytest.mark.asyncio
async def test_reinstate_read_never_selects_other_pauses_or_principals(db_client):
    await _seed_job(db_client, "user_paused", BANNED, status="paused", paused_reason="user")
    await _seed_job(db_client, "null_reason", BANNED, status="paused")
    await _seed_job(db_client, "empty_reason", BANNED, status="paused", paused_reason="")
    await _seed_job(db_client, "quota", BANNED, status="paused_no_quota", paused_reason="banned")
    await _seed_job(db_client, "active", BANNED, status="active")
    await _seed_job(db_client, "runs_as_other", BANNED, status="paused", paused_reason="banned",
                    related_entity_id=OTHER)
    await _seed_job(db_client, "other", OTHER, status="paused", paused_reason="banned")

    assert await _ids(db_client) == []
    assert await _ids(db_client, reasons=()) == []


@pytest.mark.asyncio
async def test_suspend_then_reinstate_read_is_the_same_population(db_client):
    await _seed_job(db_client, "a", BANNED, status="active")
    await _seed_job(db_client, "b", OTHER, status="pending", related_entity_id=BANNED)
    await _seed_job(db_client, "mine", BANNED, status="paused", paused_reason="user")
    await _seed_job(db_client, "waits", BANNED, status="blocked")
    await _seed_job(db_client, "held", BANNED, status="blocked_failed")
    repo = JobRepository(db_client)

    assert await repo.pause_jobs_for_execution_principal(BANNED, "banned") == 2
    assert await _ids(db_client) == ["a", "b"]
    assert await _status(db_client, "waits") == ("blocked", None)
    assert await _status(db_client, "held") == ("blocked_failed", None)
