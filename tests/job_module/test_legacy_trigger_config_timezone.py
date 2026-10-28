"""
@file_name: test_legacy_trigger_config_timezone.py
@author: Bin Liang
@date: 2026-09-09
@description: B-15 — legacy job rows whose trigger_config was written before
the timezone-required validator existed (e.g. `{'cron': '0 13 * * 1-5'}` with
no `timezone` key) must still load through JobRepository instead of raising a
pydantic ValidationError. The default is applied in memory only — the stored
row is never rewritten.

No new raw SQL is introduced by this fix (it is pure Python-side JSON→model
construction), so this suite is SQLite-only; the fix does not depend on
dialect-specific column behavior the way a schema/DDL change would.
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository import JobRepository


async def _insert_legacy_job(db, job_id, trigger_config_json):
    await db.insert("instance_jobs", {
        "job_id": job_id,
        "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1",
        "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": trigger_config_json,
        "status": "active",
        "notification_method": "inbox",
    })


@pytest.mark.asyncio
async def test_get_job_loads_legacy_row_missing_timezone(db_client):
    await _insert_legacy_job(db_client, "job_legacy_1", '{"cron":"0 13 * * 1-5"}')

    repo = JobRepository(db_client)
    job = await repo.get_job("job_legacy_1")

    assert job is not None
    assert job.trigger_config.cron == "0 13 * * 1-5"
    assert job.trigger_config.timezone == "UTC"


@pytest.mark.asyncio
async def test_get_job_keeps_existing_timezone(db_client):
    await _insert_legacy_job(
        db_client, "job_with_tz",
        '{"cron":"0 13 * * 1-5","timezone":"Asia/Shanghai"}',
    )

    repo = JobRepository(db_client)
    job = await repo.get_job("job_with_tz")

    assert job.trigger_config.timezone == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_loading_a_legacy_row_does_not_rewrite_it(db_client):
    """The default is an in-memory fill-in only — the stored row must be left
    exactly as it was written, not silently mutated with a guessed timezone."""
    await _insert_legacy_job(db_client, "job_legacy_2", '{"cron":"0 13 * * 1-5"}')

    repo = JobRepository(db_client)
    await repo.get_job("job_legacy_2")

    raw_row = await db_client.get_one("instance_jobs", {"job_id": "job_legacy_2"})
    assert "timezone" not in raw_row["trigger_config"]


@pytest.mark.asyncio
async def test_find_also_loads_legacy_rows(db_client):
    """`find()`-based listing paths (get_jobs_by_user, get_due_jobs, ...) all
    funnel through the same `_row_to_entity` — proven here via get_jobs_by_user
    so the fix isn't pinned to a single call site."""
    await _insert_legacy_job(db_client, "job_legacy_3", '{"interval_seconds":3600}')

    repo = JobRepository(db_client)
    jobs = await repo.get_jobs_by_user("user_1")

    assert any(j.job_id == "job_legacy_3" for j in jobs)
    legacy = next(j for j in jobs if j.job_id == "job_legacy_3")
    assert legacy.trigger_config.timezone == "UTC"
