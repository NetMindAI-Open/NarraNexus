"""
@file_name: test_schedule_horizon.py
@author: Bin Liang
@date: 2026-08-19
@description: TriggerConfig.end_at — the platform-enforced scheduling horizon
for recurring jobs ("this schedule runs until date X"). Born with the
onboarding guide agent's daily check-in (whose only hard stop used to be a
sentence in the payload asking the model to pause itself), but generic:
trial-period reminders, countdowns, N-day courses all need the same
primitive. Pins: the schema contract (naive + timezone required), the pure
horizon predicate, and the trigger finalize path completing — not
rescheduling — a job whose next fire lands past its horizon, while a
horizon-free job behaves exactly as before.
"""
from datetime import datetime, timezone as dt_tz

import pytest

from narranexus.platform.utils.job_scheduling import past_schedule_horizon
from narranexus_plugins.job_module.job_trigger import JobTrigger
from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import JobStatus, TriggerConfig


# ── schema contract ──────────────────────────────────────────────────────────

def test_end_at_must_be_naive():
    with pytest.raises(ValueError, match="naive"):
        TriggerConfig(
            interval_seconds=86400,
            timezone="UTC",
            end_at=datetime(2026, 9, 2, tzinfo=dt_tz.utc),
        )


def test_end_at_alone_requires_timezone():
    with pytest.raises(ValueError, match="timezone is required"):
        TriggerConfig(end_at=datetime(2026, 9, 2))


def test_end_at_is_optional_and_defaults_none():
    tc = TriggerConfig(interval_seconds=3600, timezone="UTC")
    assert tc.end_at is None  # pre-existing configs parse unchanged


# ── horizon predicate ────────────────────────────────────────────────────────

def test_past_schedule_horizon_predicate():
    tc = TriggerConfig(
        interval_seconds=86400,
        timezone="Asia/Shanghai",
        end_at=datetime(2026, 9, 2, 8, 0, 0),  # naive local (UTC+8) = 00:00 UTC
    )
    assert past_schedule_horizon(tc, datetime(2026, 9, 2, 1, 0, tzinfo=dt_tz.utc)) is True
    assert past_schedule_horizon(tc, datetime(2026, 9, 1, 23, 0, tzinfo=dt_tz.utc)) is False
    # No horizon / no config → never trips (pre-existing jobs untouched).
    assert past_schedule_horizon(
        TriggerConfig(interval_seconds=60, timezone="UTC"),
        datetime(2099, 1, 1, tzinfo=dt_tz.utc),
    ) is False
    assert past_schedule_horizon(None, datetime(2099, 1, 1, tzinfo=dt_tz.utc)) is False


# ── trigger finalize path ────────────────────────────────────────────────────

async def _insert_scheduled_job(db, job_id, trigger_config: str):
    now = datetime(2026, 8, 19, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": trigger_config,
        "status": "running", "notification_method": "inbox",
        "created_at": now, "updated_at": now,
    })


@pytest.mark.asyncio
async def test_finalize_completes_scheduled_job_past_horizon(db_client):
    # end_at is in the past, so the next fire (now + 1 day) is past the
    # horizon: the platform must complete the job, NOT reschedule it.
    await _insert_scheduled_job(
        db_client, "job_h1",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_h1")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_h1"})
    assert row["status"] == JobStatus.COMPLETED.value
    assert row["next_run_time"] is None  # nothing left for the poller to fire


@pytest.mark.asyncio
async def test_finalize_reschedules_before_horizon(db_client):
    # Horizon far in the future: behavior identical to a horizon-free job.
    await _insert_scheduled_job(
        db_client, "job_h2",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2099-01-01T00:00:00"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_h2")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_h2"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["next_run_time"] is not None


@pytest.mark.asyncio
async def test_finalize_without_horizon_is_unchanged(db_client):
    await _insert_scheduled_job(
        db_client, "job_h3", '{"interval_seconds":86400,"timezone":"UTC"}'
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_h3")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_h3"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["next_run_time"] is not None


# ── in-run status changes are respected (the self-pause exit path) ──────────


async def _mark_status_mid_run(db, job_id: str, status: str):
    """Simulate an in-run job_update (agent self-pause / user cancel): the
    trigger holds a pre-execution snapshot while the DB row moves on."""
    await db.update("instance_jobs", {"job_id": job_id}, {"status": status})


@pytest.mark.asyncio
async def test_finalize_respects_agent_self_pause(db_client):
    # The onboarding guide's payload tells the model to pause THIS job after
    # a goodbye. finalize used to overwrite that with ACTIVE + tomorrow's
    # next_run — "I'll stop reaching out" followed by a ping the next day.
    await _insert_scheduled_job(
        db_client, "job_p1", '{"interval_seconds":86400,"timezone":"UTC"}'
    )
    # A stale next_run from before the run started (try_acquire_job doesn't
    # clear it) — without seeding this, the clear-next_run assertion below
    # would pass vacuously against the fixture's NULL.
    await db_client.update(
        "instance_jobs", {"job_id": "job_p1"},
        {"next_run_time": "2026-08-19T00:00:00Z"},
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_p1")  # snapshot: running
    await _mark_status_mid_run(db_client, "job_p1", "paused")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "goodbye"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_p1"})
    assert row["status"] == JobStatus.PAUSED.value  # NOT resurrected to active
    assert row["next_run_time"] is None  # stale pre-run next_run cleared too
    assert row["last_run_time"] is not None  # the run itself is still on record


@pytest.mark.asyncio
async def test_horizon_does_not_stamp_completed_over_inrun_cancel(db_client):
    # Past-horizon job whose user cancelled it DURING the run: the respect
    # branch must win — COMPLETED must not overwrite CANCELLED.
    await _insert_scheduled_job(
        db_client, "job_p2",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_p2")
    await _mark_status_mid_run(db_client, "job_p2", "cancelled")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_p2"})
    assert row["status"] == "cancelled"


# ── re-arm paths also honor the horizon (no extra fire through side doors) ──


@pytest.mark.asyncio
async def test_rearm_cooling_completes_past_horizon(db_client):
    # A failed run's retry would fire past end_at: the failure-backoff door
    # must not leak an extra run — complete instead of re-arming.
    now = datetime(2026, 8, 19, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db_client.insert("instance_jobs", {
        "job_id": "job_r1", "instance_id": "ins_job_r1",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00"}',
        "status": "cooling", "consecutive_failure_count": 1,
        "cooldown_until": "2026-01-02T00:00:00Z",  # elapsed AND past horizon
        "created_at": now, "updated_at": now,
    })
    trigger = JobTrigger(database_client=db_client)

    await trigger._rearm_cooled_jobs()

    row = await db_client.get_one("instance_jobs", {"job_id": "job_r1"})
    assert row["status"] == JobStatus.COMPLETED.value
    assert row["next_run_time"] is None


@pytest.mark.asyncio
async def test_rearm_cooling_without_horizon_still_rearms(db_client):
    now = datetime(2026, 8, 19, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db_client.insert("instance_jobs", {
        "job_id": "job_r2", "instance_id": "ins_job_r2",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": '{"interval_seconds":86400,"timezone":"UTC"}',
        "status": "cooling", "consecutive_failure_count": 1,
        "cooldown_until": "2026-01-02T00:00:00Z",  # elapsed
        "created_at": now, "updated_at": now,
    })
    trigger = JobTrigger(database_client=db_client)

    await trigger._rearm_cooled_jobs()

    row = await db_client.get_one("instance_jobs", {"job_id": "job_r2"})
    assert row["status"] == JobStatus.ACTIVE.value  # existing behavior intact


@pytest.mark.asyncio
async def test_heal_zombie_completes_past_horizon(db_client):
    # ACTIVE + NULL next_run zombie whose recomputed fire lands past end_at:
    # completing it beats resurrecting a schedule that owes no more fires.
    now = datetime(2026, 8, 19, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db_client.insert("instance_jobs", {
        "job_id": "job_z1", "instance_id": "ins_job_z1",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00"}',
        "status": "active", "created_at": now, "updated_at": now,
    })
    trigger = JobTrigger(database_client=db_client)

    await trigger._heal_unscheduled_active_jobs()

    row = await db_client.get_one("instance_jobs", {"job_id": "job_z1"})
    assert row["status"] == JobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_heal_zombie_without_horizon_still_heals(db_client):
    now = datetime(2026, 8, 19, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db_client.insert("instance_jobs", {
        "job_id": "job_z2", "instance_id": "ins_job_z2",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": '{"interval_seconds":86400,"timezone":"UTC"}',
        "status": "active", "created_at": now, "updated_at": now,
    })
    trigger = JobTrigger(database_client=db_client)

    await trigger._heal_unscheduled_active_jobs()

    row = await db_client.get_one("instance_jobs", {"job_id": "job_z2"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["next_run_time"] is not None  # existing behavior intact


# ── the horizon is generic to recurring types, and ONLY recurring types ──────


async def _insert_job_of_type(db, job_id, job_type, trigger_config, status="running", **extra):
    now = datetime(2026, 8, 19, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    row = {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": job_type, "trigger_config": trigger_config,
        "status": status, "notification_method": "inbox",
        "created_at": now, "updated_at": now,
    }
    row.update(extra)
    await db.insert("instance_jobs", row)


@pytest.mark.asyncio
async def test_ongoing_mechanical_fallback_completes_past_horizon(db_client):
    # end_at is model-writable on any type via the shared MCP schema; an
    # ONGOING job (the type whose end_condition is model-judged and thus most
    # needs a platform brake) must honor it on the mechanical-fallback path,
    # not silently ignore it while the Jobs panel shows "Runs until: ...".
    await _insert_job_of_type(
        db_client, "job_o1", "ongoing",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00","end_condition":"user says stop"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_o1")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_o1"})
    assert row["status"] == JobStatus.COMPLETED.value
    assert row["next_run_time"] is None
    assert row["iteration_count"] == 1  # the iteration still counted


@pytest.mark.asyncio
async def test_ongoing_without_horizon_falls_back_unchanged(db_client):
    await _insert_job_of_type(
        db_client, "job_o2", "ongoing",
        '{"interval_seconds":86400,"timezone":"UTC","end_condition":"user says stop"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_o2")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_o2"})
    assert row["status"] == JobStatus.ACTIVE.value  # existing behavior intact
    assert row["next_run_time"] is not None


@pytest.mark.asyncio
async def test_rearm_cooling_one_off_ignores_horizon(db_client):
    # A ONE_OFF has no "next fire" for a horizon to bound: its single run has
    # not succeeded yet, and completing it here would mark a never-delivered
    # reminder as done. It must re-arm normally even with a stale end_at.
    await _insert_job_of_type(
        db_client, "job_oo1", "one_off",
        '{"run_at":"2026-08-18T00:00:00","timezone":"UTC","end_at":"2026-01-01T00:00:00"}',
        status="cooling", consecutive_failure_count=1,
        cooldown_until="2026-01-02T00:00:00Z",
    )
    trigger = JobTrigger(database_client=db_client)

    await trigger._rearm_cooled_jobs()

    row = await db_client.get_one("instance_jobs", {"job_id": "job_oo1"})
    assert row["status"] == JobStatus.ACTIVE.value  # retried, NOT completed


# ── ONGOING normal (hook-owned) path also honors the horizon ─────────────────


@pytest.mark.asyncio
async def test_ongoing_hook_reschedule_past_horizon_completes(db_client):
    # The NORMAL ongoing path: after_turn took over (status
    # moved off RUNNING) and rescheduled a fire that lands past end_at. The
    # horizon is not a call the LLM makes (it only judges end_condition), so
    # finalize must complete the job instead of "respecting" a fire the
    # schedule no longer owes. Distinct from the mechanical-fallback tests
    # above, which all leave status="running".
    await _insert_job_of_type(
        db_client, "job_o3", "ongoing",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00","end_condition":"x"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_o3")  # snapshot: running
    # Simulate the hook: status active + a rescheduled (past-horizon) fire.
    await db_client.update(
        "instance_jobs", {"job_id": "job_o3"},
        {"status": "active", "next_run_time": "2026-08-20T00:00:00Z"},
    )

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_o3"})
    assert row["status"] == JobStatus.COMPLETED.value
    assert row["next_run_time"] is None


@pytest.mark.asyncio
async def test_ongoing_hook_reschedule_before_horizon_is_respected(db_client):
    # Hook rescheduled a fire BEFORE the horizon: finalize must respect the
    # hook's decision (leave it active), not complete it.
    await _insert_job_of_type(
        db_client, "job_o4", "ongoing",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2099-01-01T00:00:00","end_condition":"x"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_o4")
    await db_client.update(
        "instance_jobs", {"job_id": "job_o4"},
        {"status": "active", "next_run_time": "2026-08-20T00:00:00Z"},
    )

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_o4"})
    assert row["status"] == JobStatus.ACTIVE.value  # hook's decision respected
    assert row["next_run_time"] is not None


@pytest.mark.asyncio
async def test_ongoing_hook_completed_is_not_double_completed(db_client):
    # Hook already finished the job (COMPLETED, next_run cleared): finalize
    # must NOT re-complete it — re-firing instance completion would push the
    # ModulePoller dependency chain a second time. The cleared next_run (None)
    # is the skip signal. Asserting only status==COMPLETED was vacuous (the row
    # is COMPLETED either way); spy the instance-completion call so a
    # regression that treats a None next_run as "horizon reached → complete"
    # (or otherwise re-completes here) turns red.
    from unittest.mock import AsyncMock

    await _insert_job_of_type(
        db_client, "job_o5", "ongoing",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00","end_condition":"x"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_o5")
    await db_client.update(
        "instance_jobs", {"job_id": "job_o5"},
        {"status": "completed", "next_run_time": None},
    )
    spy = AsyncMock()
    trigger._update_instance_completed = spy  # type: ignore[method-assign]

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_o5"})
    assert row["status"] == JobStatus.COMPLETED.value  # unchanged, no error
    spy.assert_not_called()  # no SECOND instance completion


@pytest.mark.asyncio
async def test_ongoing_hook_reschedule_respects_inrun_cancel(db_client):
    # A user cancelled the ONGOING job mid-run while a stale/hook-set
    # next_run_time still points past the horizon. The else (hook-owned) branch
    # must NOT stamp COMPLETED over CANCELLED — same _IN_RUN_STOPS guard the
    # SCHEDULED branch has — and must not fire instance completion. Deleting the
    # guard turns this red (status becomes COMPLETED, spy called once).
    from unittest.mock import AsyncMock

    await _insert_job_of_type(
        db_client, "job_o6", "ongoing",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00","end_condition":"x"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_o6")  # snapshot: running
    # Simulate the user cancelling mid-run; a crossed next_run lingers on the row.
    await db_client.update(
        "instance_jobs", {"job_id": "job_o6"},
        {"status": "cancelled", "next_run_time": "2026-08-20T00:00:00Z"},
    )
    spy = AsyncMock()
    trigger._update_instance_completed = spy  # type: ignore[method-assign]

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_o6"})
    assert row["status"] == "cancelled"  # in-run stop wins, NOT COMPLETED
    spy.assert_not_called()
