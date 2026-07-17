"""
@file_name: test_job_auth_recovery.py
@author:
@date: 2026-07-13
@description: Auth failures are recoverable, not terminal; reactivation reschedules.

Incident 2026-07-13: a daily cron job hit "Claude API authentication failed",
which was mis-classified as a transient failure, escalated to terminal FAILED
after _MAX_CONSECUTIVE_FAILURES tries (FAILED has no recovery path), and a later
``status='active'`` rule-update left ``next_run_time`` NULL — an "active but
never scheduled" zombie. These tests lock in the three fixes:
  1. auth failures pause (PAUSED_NO_QUOTA), never escalate to terminal FAILED;
  2. reactivating a job to ACTIVE recomputes next_run + clears the failure state;
  3. the poller self-heals any ACTIVE scheduled job left with a NULL next_run.
"""
from datetime import datetime, timezone as dt_tz

import pytest

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.module.job_module.job_trigger import (
    JobTrigger,
    _is_auth_failure,
    _MAX_CONSECUTIVE_FAILURES,
)

SCHEDULED_TRIGGER = '{"cron":"0 11 * * *","timezone":"America/New_York"}'


async def _insert_job(db, job_id, status="active", **extra):
    now = datetime(2026, 7, 13, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    row = {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": status, "notification_method": "inbox",
        "created_at": now, "updated_at": now,
    }
    row.update(extra)
    await db.insert("instance_jobs", row)


# ── Fix 1: auth-failure detection ─────────────────────────────────────────────

def test_is_auth_failure_by_error_type():
    assert _is_auth_failure({"success": False, "error_type": "auth_expired"})
    assert _is_auth_failure({"success": False, "error_type": "AuthenticationError"})


def test_is_auth_failure_by_message():
    assert _is_auth_failure({
        "success": False,
        "error": "Claude API authentication failed. Please check your API key.",
    })
    assert _is_auth_failure({"success": False, "error": "Not logged in · Please run /login"})


def test_is_auth_failure_false_for_success_and_transient():
    assert not _is_auth_failure({"success": True})
    assert not _is_auth_failure({"success": False, "error_type": "TimeoutError", "error": "read timed out"})
    assert not _is_auth_failure({"success": False, "error_type": "ConnectionError", "error": "boom"})


# ── Fix 1: auth failure pauses (recoverable), never terminal FAILED ────────────

@pytest.mark.asyncio
async def test_finalize_pauses_on_auth_failure(db_client):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_auth1")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_auth1")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "auth_expired",
        "error": "Claude API authentication failed. Please check your API key.",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_auth1"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value


@pytest.mark.asyncio
async def test_repeated_auth_failures_never_escalate_to_failed(db_client):
    """Even at the transient cap, an auth failure must stay recoverable — not
    tip into terminal FAILED (which had no recovery path)."""
    repo = JobRepository(db_client)
    await _insert_job(
        db_client, "job_auth2",
        consecutive_failure_count=_MAX_CONSECUTIVE_FAILURES - 1,
    )
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_auth2")

    await trigger._finalize_job_execution(job, {
        "success": False, "error_type": "auth_expired",
        "error": "authentication failed", "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_auth2"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value
    assert row["status"] != JobStatus.FAILED.value


# ── Fix 2: reactivation recomputes next_run + clears failure state ─────────────

@pytest.mark.asyncio
async def test_reactivating_zombie_recomputes_next_run_and_clears_failure(db_client):
    from xyz_agent_context.module.job_module.job_service import JobInstanceService

    # Zombie: active but next_run NULL, stale pause/failure state.
    await _insert_job(
        db_client, "job_z1", status="active",
        paused_reason="repeated_failure", consecutive_failure_count=8,
    )
    svc = JobInstanceService(db_client)
    await svc.update_job("job_z1", {"status": JobStatus.ACTIVE}, agent_id="agent_1")

    row = await db_client.get_one("instance_jobs", {"job_id": "job_z1"})
    assert row["next_run_time"] is not None            # recomputed → schedulable again
    assert row["paused_reason"] in (None, "")          # cleared
    assert (row["consecutive_failure_count"] or 0) == 0


# ── Fix 3: poller self-heals active + NULL next_run zombies ────────────────────

@pytest.mark.asyncio
async def test_self_heal_unscheduled_active_job(db_client):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_h1", status="active")  # next_run NULL
    trigger = JobTrigger(database_client=db_client)

    healed = await trigger._heal_unscheduled_active_jobs()
    assert healed >= 1

    row = await db_client.get_one("instance_jobs", {"job_id": "job_h1"})
    assert row["next_run_time"] is not None


@pytest.mark.asyncio
async def test_self_heal_ignores_active_jobs_that_already_have_next_run(db_client):
    repo = JobRepository(db_client)
    await _insert_job(
        db_client, "job_h2", status="active",
        next_run_time="2026-07-14T15:00:00Z",
    )
    trigger = JobTrigger(database_client=db_client)
    healed = await trigger._heal_unscheduled_active_jobs()
    # job_h2 is already scheduled → must not be counted/changed.
    row = await db_client.get_one("instance_jobs", {"job_id": "job_h2"})
    assert row["next_run_time"] is not None
