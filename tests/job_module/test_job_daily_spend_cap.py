"""
@file_name: test_job_daily_spend_cap.py
@author: Bin Liang
@date: 2026-09-09
@description: B-14 — user-level daily spend circuit for scheduled/ongoing
jobs. A user burned ~$140 in 4 days on two 2h heartbeat jobs under
nexus_power (1-7M input tokens/run). NARRANEXUS_USER_DAILY_SPEND_CAP_USD
(0/unset = disabled) gates the NEXT scheduled start — it never interrupts a
run already in flight. The cap counts the user's WHOLE LLM spend for the
day (review I6), and "day" is the job's frozen timezone, not UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_tz

import pytest

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import JobStatus
import narranexus_plugins.job_module.job_trigger as trigger_mod
from narranexus_plugins.job_module.job_trigger import (
    JobTrigger,
    _daily_spend_usd_for_user,
    local_day_start_utc,
)

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'
ENV_VAR = "NARRANEXUS_USER_DAILY_SPEND_CAP_USD"

# 20:00Z — 04:00 next day in Asia/Shanghai (UTC+8), 16:00 same day in
# America/New_York (UTC-4 in September). Chosen so the three zones disagree
# about which UTC instant "today" started at.
FIXED_NOW = datetime(2026, 9, 10, 20, 0, 0, tzinfo=dt_tz.utc)


async def _insert_job(db, job_id, user_id="user_1", status="active", trigger_config=SCHEDULED_TRIGGER):
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id,
        "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1",
        "user_id": user_id,
        "title": "Heartbeat job", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": trigger_config,
        "status": status,
        "notification_method": "inbox",
        "next_run_time": "2020-01-01T00:00:00Z",
        "created_at": now,
        "updated_at": now,
    })


async def _insert_cost_record(db, user_id, cost_usd, created_at=None):
    await db.insert("cost_records", {
        "agent_id": "agent_1",
        "call_type": "agent_loop",
        "model": "test-model",
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_cost_usd": cost_usd,
        "user_id": user_id,
        "created_at": created_at or datetime.now(dt_tz.utc),
    })


# ── pure sum helper (dialect-portable raw SQL) ──────────────────────────────

@pytest.mark.asyncio
async def test_sums_only_todays_records_for_the_user(db_client):
    await _insert_cost_record(db_client, "user_1", 1.5)
    await _insert_cost_record(db_client, "user_1", 2.25)
    # Yesterday — must not count.
    yesterday = datetime.now(dt_tz.utc) - timedelta(days=1)
    await _insert_cost_record(db_client, "user_1", 100.0, created_at=yesterday)
    # A different user — must not count.
    await _insert_cost_record(db_client, "user_2", 50.0)

    total = await _daily_spend_usd_for_user(db_client, "user_1")

    assert total == pytest.approx(3.75)


@pytest.mark.asyncio
async def test_no_records_returns_zero(db_client):
    assert await _daily_spend_usd_for_user(db_client, "user_nobody") == 0.0


# ── "today" is the job's local day, not the UTC day (review I6) ─────────────

def test_local_day_start_follows_the_given_timezone():
    assert local_day_start_utc("UTC", FIXED_NOW) == datetime(2026, 9, 10, 0, 0, tzinfo=dt_tz.utc)
    # 04:00 on the 11th locally -> local midnight of the 11th = 16:00Z on the 10th.
    assert local_day_start_utc("Asia/Shanghai", FIXED_NOW) == datetime(2026, 9, 10, 16, 0, tzinfo=dt_tz.utc)
    # 16:00 on the 10th locally -> local midnight of the 10th = 04:00Z on the 10th.
    assert local_day_start_utc("America/New_York", FIXED_NOW) == datetime(2026, 9, 10, 4, 0, tzinfo=dt_tz.utc)


def test_local_day_start_falls_back_to_utc_for_an_unknown_zone():
    assert local_day_start_utc("Not/AZone", FIXED_NOW) == local_day_start_utc("UTC", FIXED_NOW)


@pytest.mark.asyncio
async def test_sums_from_the_local_midnight_of_the_jobs_timezone(db_client):
    """A record at 15:59Z is still "today" in UTC but "yesterday" in
    Asia/Shanghai (local midnight = 16:00Z); one at 16:01Z is today in both."""
    before_local_midnight = datetime(2026, 9, 10, 15, 59, tzinfo=dt_tz.utc)
    after_local_midnight = datetime(2026, 9, 10, 16, 1, tzinfo=dt_tz.utc)
    await _insert_cost_record(db_client, "user_1", 1.0, created_at=before_local_midnight)
    await _insert_cost_record(db_client, "user_1", 2.0, created_at=after_local_midnight)
    # SQLite keeps `created_at` as text in TWO shapes: ISO `T` form for
    # explicit datetime writes (the two above) and space-separated form from
    # the column default. Both shapes on the boundary date must be judged by
    # their instant, not by string order (`T` sorts after the space).
    await _insert_cost_record(db_client, "user_1", 3.0, created_at="2026-09-10 16:30:00")
    await _insert_cost_record(db_client, "user_1", 7.0, created_at="2026-09-10 15:30:00")

    shanghai = await _daily_spend_usd_for_user(db_client, "user_1", "Asia/Shanghai", now=FIXED_NOW)
    utc = await _daily_spend_usd_for_user(db_client, "user_1", "UTC", now=FIXED_NOW)

    assert shanghai == pytest.approx(5.0)
    assert utc == pytest.approx(13.0)


@pytest.mark.asyncio
async def test_default_timestamp_rows_are_counted(db_client):
    """cost_tracker never writes created_at — the column default fills it
    (SQLite: `datetime('now')`, space-separated UTC text). The cutoff must
    compare correctly against that shape, not only against ISO 'T' writes."""
    await db_client.insert("cost_records", {
        "agent_id": "agent_1", "call_type": "agent_loop", "model": "m",
        "input_tokens": 1, "output_tokens": 1, "total_cost_usd": 4.0,
        "user_id": "user_default_ts",
    })
    assert await _daily_spend_usd_for_user(db_client, "user_default_ts", "UTC") == pytest.approx(4.0)


# ── cap gate + pause wiring ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_by_default(db_client, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    await _insert_cost_record(db_client, "user_1", 99999.0)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._daily_spend_cap_exceeded("user_1") is False


@pytest.mark.asyncio
async def test_cap_exceeded_when_spend_meets_the_threshold(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "10")
    await _insert_cost_record(db_client, "user_1", 10.0)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._daily_spend_cap_exceeded("user_1") is True


@pytest.mark.asyncio
async def test_cap_not_exceeded_when_under_threshold(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "10")
    await _insert_cost_record(db_client, "user_1", 5.0)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._daily_spend_cap_exceeded("user_1") is False


@pytest.mark.asyncio
async def test_execute_job_pauses_with_spend_cap_status(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    await _insert_cost_record(db_client, "user_1", 5.0)
    await _insert_job(db_client, "job_over_cap")
    repo = JobRepository(db_client)
    job = await repo.get_job("job_over_cap")
    trigger = JobTrigger(database_client=db_client)

    async def _boom(*a, **k):
        raise AssertionError("must not call the framework once the cap is exceeded")
    monkeypatch.setattr(trigger, "_run_agent", _boom)

    await trigger._execute_job(job)

    row = await db_client.get_one("instance_jobs", {"job_id": "job_over_cap"})
    assert row["status"] == JobStatus.PAUSED_SPEND_CAP.value
    assert row["paused_reason"] == "spend_cap"


@pytest.mark.asyncio
async def test_execute_job_runs_normally_under_cap(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "100")
    await _insert_cost_record(db_client, "user_1", 5.0)
    await _insert_job(db_client, "job_under_cap")
    repo = JobRepository(db_client)
    job = await repo.get_job("job_under_cap")
    trigger = JobTrigger(database_client=db_client)

    called = {}

    async def _fake_run_agent(job_arg, prompt):
        called["ran"] = True
        return {"success": True, "output": "ok", "event_id": None}

    monkeypatch.setattr(trigger, "_run_agent", _fake_run_agent)

    await trigger._execute_job(job)

    assert called.get("ran") is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_under_cap"})
    assert row["status"] != JobStatus.PAUSED_SPEND_CAP.value


@pytest.mark.asyncio
async def test_execute_job_judges_today_in_the_jobs_timezone(db_client, monkeypatch):
    """Job frozen to Asia/Shanghai; spend booked at 15:30Z — inside the UTC
    day but before local midnight (16:00Z). Under a UTC day boundary the cap
    would already be hit and the job paused; under the job's own day it has
    spent nothing yet and must run."""
    monkeypatch.setenv(ENV_VAR, "1")
    monkeypatch.setattr(trigger_mod, "utc_now", lambda: FIXED_NOW)
    await _insert_cost_record(
        db_client, "user_1", 5.0, created_at=datetime(2026, 9, 10, 15, 30, tzinfo=dt_tz.utc)
    )
    await _insert_job(db_client, "job_tz_runs")
    job = await JobRepository(db_client).get_job("job_tz_runs")
    trigger = JobTrigger(database_client=db_client)
    called = {}

    async def _fake_run_agent(job_arg, prompt):
        called["ran"] = True
        return {"success": True, "output": "ok", "event_id": None}

    monkeypatch.setattr(trigger, "_run_agent", _fake_run_agent)

    await trigger._execute_job(job)

    assert called.get("ran") is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_tz_runs"})
    assert row["status"] != JobStatus.PAUSED_SPEND_CAP.value


@pytest.mark.asyncio
async def test_execute_job_pauses_when_spend_is_inside_the_local_day(db_client, monkeypatch):
    """Same job, spend booked at 16:30Z — after Asia/Shanghai's local
    midnight — so it counts and the cap trips."""
    monkeypatch.setenv(ENV_VAR, "1")
    monkeypatch.setattr(trigger_mod, "utc_now", lambda: FIXED_NOW)
    await _insert_cost_record(
        db_client, "user_1", 5.0, created_at=datetime(2026, 9, 10, 16, 30, tzinfo=dt_tz.utc)
    )
    await _insert_job(db_client, "job_tz_paused")
    job = await JobRepository(db_client).get_job("job_tz_paused")
    trigger = JobTrigger(database_client=db_client)

    async def _boom(*a, **k):
        raise AssertionError("must not call the framework once the cap is exceeded")
    monkeypatch.setattr(trigger, "_run_agent", _boom)

    await trigger._execute_job(job)

    row = await db_client.get_one("instance_jobs", {"job_id": "job_tz_paused"})
    assert row["status"] == JobStatus.PAUSED_SPEND_CAP.value


# ── C1: the cap is a per-day ceiling — the backstop brings the job back ─────

async def _insert_capped_job(db, job_id, user_id="user_1", trigger_config=SCHEDULED_TRIGGER):
    await _insert_job(
        db, job_id, user_id=user_id, status=JobStatus.PAUSED_SPEND_CAP.value,
        trigger_config=trigger_config,
    )
    await db.update("instance_jobs", {"job_id": job_id}, {
        "paused_reason": "spend_cap", "paused_at": FIXED_NOW,
    })


@pytest.mark.asyncio
async def test_backstop_resumes_a_spend_capped_job_once_under_the_cap(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "100")
    await _insert_cost_record(db_client, "user_1", 5.0)
    await _insert_capped_job(db_client, "job_resume")
    trigger = JobTrigger(database_client=db_client)

    resumed = await trigger._resume_spend_capped_jobs()

    assert resumed == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_resume"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["paused_reason"] is None
    # Schedule goes FORWARD from now — the stale 2020 next_run must not replay.
    assert row["next_run_time"] > datetime.now(dt_tz.utc)


@pytest.mark.asyncio
async def test_backstop_keeps_the_job_paused_while_still_over_the_cap(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    await _insert_cost_record(db_client, "user_1", 5.0)
    await _insert_capped_job(db_client, "job_still_over")
    trigger = JobTrigger(database_client=db_client)

    resumed = await trigger._resume_spend_capped_jobs()

    assert resumed == 0
    row = await db_client.get_one("instance_jobs", {"job_id": "job_still_over"})
    assert row["status"] == JobStatus.PAUSED_SPEND_CAP.value
    assert row["paused_reason"] == "spend_cap"


@pytest.mark.asyncio
async def test_backstop_resumes_when_ops_disable_the_cap(db_client, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    await _insert_cost_record(db_client, "user_1", 99999.0)
    await _insert_capped_job(db_client, "job_cap_off")
    trigger = JobTrigger(database_client=db_client)

    assert await trigger._resume_spend_capped_jobs() == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_cap_off"})
    assert row["status"] == JobStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_poll_cycle_runs_the_spend_cap_backstop(db_client, monkeypatch):
    """Wiring: the first poll cycle (backstop due) revives a capped job whose
    user is under the cap, without anyone touching the Jobs panel."""
    monkeypatch.setenv(ENV_VAR, "100")
    await _insert_capped_job(db_client, "job_via_poll")
    trigger = JobTrigger(database_client=db_client)

    await trigger._poll_and_enqueue()

    row = await db_client.get_one("instance_jobs", {"job_id": "job_via_poll"})
    assert row["status"] == JobStatus.ACTIVE.value


# ── review r2 M-d: horizon and per-job timezone in the backstop ─────────────

@pytest.mark.asyncio
async def test_backstop_completes_a_job_whose_next_fire_is_past_end_at(db_client, monkeypatch):
    """Under the cap, but the schedule's horizon has passed while the job sat
    capped: it completes (next_run cleared, instance completed) instead of
    being re-armed for a fire the schedule no longer owes."""
    from narranexus.platform.repository import InstanceRepository
    from narranexus.platform.schema.instance_schema import InstanceStatus, ModuleInstanceRecord

    monkeypatch.setenv(ENV_VAR, "100")
    await InstanceRepository(db_client).create_instance(ModuleInstanceRecord(
        instance_id="ins_job_expired", module_class="JobModule", agent_id="agent_1",
        status=InstanceStatus.ACTIVE, dependencies=[],
    ))
    await _insert_capped_job(
        db_client, "job_expired",
        trigger_config='{"cron":"0 8 * * *","timezone":"Asia/Shanghai","end_at":"2021-01-01T00:00:00"}',
    )
    trigger = JobTrigger(database_client=db_client)

    assert await trigger._resume_spend_capped_jobs() == 0

    row = await db_client.get_one("instance_jobs", {"job_id": "job_expired"})
    assert row["status"] == JobStatus.COMPLETED.value
    assert row["paused_reason"] is None
    assert row["next_run_time"] is None
    inst = await InstanceRepository(db_client).get_by_instance_id("ins_job_expired")
    assert inst.status == InstanceStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_backstop_judges_each_jobs_own_timezone(db_client, monkeypatch):
    """$5 booked at 15:30Z against a $1 cap. For the Asia/Shanghai job that is
    yesterday (local midnight = 16:00Z) so it resumes; for the UTC job it is
    today so it stays capped. A single UTC day would treat both the same."""
    monkeypatch.setenv(ENV_VAR, "1")
    monkeypatch.setattr(trigger_mod, "utc_now", lambda: FIXED_NOW)
    await _insert_cost_record(
        db_client, "user_1", 5.0, created_at=datetime(2026, 9, 10, 15, 30, tzinfo=dt_tz.utc)
    )
    await _insert_capped_job(db_client, "job_shanghai")
    await _insert_capped_job(
        db_client, "job_utc", trigger_config='{"cron":"0 8 * * *","timezone":"UTC"}',
    )
    trigger = JobTrigger(database_client=db_client)

    assert await trigger._resume_spend_capped_jobs() == 1

    shanghai = await db_client.get_one("instance_jobs", {"job_id": "job_shanghai"})
    utc = await db_client.get_one("instance_jobs", {"job_id": "job_utc"})
    assert shanghai["status"] == JobStatus.ACTIVE.value
    assert utc["status"] == JobStatus.PAUSED_SPEND_CAP.value


# ── review M1: one spend scan per (principal, timezone) per backstop call ────

@pytest.mark.asyncio
async def test_backstop_scans_each_principal_and_timezone_once(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "100")
    await _insert_cost_record(db_client, "user_1", 5.0)
    for i in range(3):
        await _insert_capped_job(db_client, f"job_sh_{i}")
    await _insert_capped_job(
        db_client, "job_utc_once", trigger_config='{"cron":"0 8 * * *","timezone":"UTC"}',
    )

    calls = []
    real = JobTrigger._daily_spend_cap_exceeded

    async def _spy(self, user_id, tz_name):
        calls.append((user_id, tz_name))
        return await real(self, user_id, tz_name)

    monkeypatch.setattr(JobTrigger, "_daily_spend_cap_exceeded", _spy)
    trigger = JobTrigger(database_client=db_client)

    assert await trigger._resume_spend_capped_jobs() == 4
    assert sorted(calls) == [("user_1", "Asia/Shanghai"), ("user_1", "UTC")]
