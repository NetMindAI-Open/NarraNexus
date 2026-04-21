"""
@file_name: test_job_repository_next_run.py
@author: Bin Liang
@date: 2026-04-21
@description: Tests for JobRepository atomic alpha+beta update methods.
"""
from datetime import datetime, timezone as dt_tz
import pytest

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.module.job_module._job_scheduling import NextRunTuple


@pytest.mark.asyncio
async def test_update_next_run_writes_all_three_fields(db_client):
    repo = JobRepository(db_client)
    await db_client.insert("instance_jobs", {
        "job_id": "job_test_1",
        "instance_id": "ins_test_1",
        "agent_id": "agent_1",
        "user_id": "user_1",
        "title": "t", "description": "d",
        "payload": "p",
        "job_type": "scheduled",
        "trigger_config": '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}',
        "status": "active",
        "notification_method": "inbox",
    })
    tup = NextRunTuple(
        local="2026-05-02T08:00:00",
        tz="Asia/Shanghai",
        utc=datetime(2026, 5, 2, 0, 0, 0, tzinfo=dt_tz.utc),
    )
    await repo.update_next_run("job_test_1", tup)
    row = await db_client.get_one("instance_jobs", {"job_id": "job_test_1"})
    assert row["next_run_at_local"] == "2026-05-02T08:00:00"
    assert row["next_run_tz"] == "Asia/Shanghai"
    assert "2026-05-02" in str(row["next_run_time"])


@pytest.mark.asyncio
async def test_update_last_run_writes_all_three_fields(db_client):
    repo = JobRepository(db_client)
    await db_client.insert("instance_jobs", {
        "job_id": "job_test_2", "instance_id": "ins_test_2",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}',
        "status": "active", "notification_method": "inbox",
    })
    now_utc = datetime(2026, 5, 1, 0, 0, 0, tzinfo=dt_tz.utc)
    await repo.update_last_run(
        "job_test_2",
        now_utc,
        "2026-05-01T08:00:00",
        "Asia/Shanghai",
    )
    row = await db_client.get_one("instance_jobs", {"job_id": "job_test_2"})
    assert row["last_run_at_local"] == "2026-05-01T08:00:00"
    assert row["last_run_tz"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_create_job_writes_beta_fields(db_client):
    from xyz_agent_context.schema.job_schema import TriggerConfig, JobType
    repo = JobRepository(db_client)
    tc = TriggerConfig(cron="0 8 * * *", timezone="Asia/Shanghai")
    await repo.create_job(
        agent_id="agent_1",
        user_id="user_1",
        job_id="job_create_beta",
        title="t",
        description="d",
        job_type=JobType.SCHEDULED,
        trigger_config=tc,
        payload="p",
        instance_id="ins_create_beta",
        notification_method="inbox",
        next_run_time=datetime(2026, 5, 2, 0, 0, 0, tzinfo=dt_tz.utc),
        next_run_at_local="2026-05-02T08:00:00",
        next_run_tz="Asia/Shanghai",
    )
    row = await db_client.get_one("instance_jobs", {"job_id": "job_create_beta"})
    assert row["next_run_at_local"] == "2026-05-02T08:00:00"
    assert row["next_run_tz"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_clear_next_run_nulls_all_three(db_client):
    repo = JobRepository(db_client)
    await db_client.insert("instance_jobs", {
        "job_id": "job_test_3", "instance_id": "ins_test_3",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "one_off",
        "trigger_config": '{"run_at":"2026-05-01T08:00:00","timezone":"Asia/Shanghai"}',
        "status": "active", "notification_method": "inbox",
        "next_run_time": "2026-05-01T00:00:00Z",
        "next_run_at_local": "2026-05-01T08:00:00",
        "next_run_tz": "Asia/Shanghai",
    })
    await repo.clear_next_run("job_test_3")
    row = await db_client.get_one("instance_jobs", {"job_id": "job_test_3"})
    assert row["next_run_time"] is None
    assert row["next_run_at_local"] is None
    assert row["next_run_tz"] is None
