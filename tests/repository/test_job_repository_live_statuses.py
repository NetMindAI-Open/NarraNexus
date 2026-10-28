"""
@file_name: test_job_repository_live_statuses.py
@author: Bin Liang
@date: 2026-09-10
@description: The four "live jobs" reads in JobRepository — duplicate-title
lookup, per-narrative and per-agent live lists, and the agent-prompt summary —
must all see every LIVE_JOB_STATUSES member (review I5).

B-16 made BLOCKED reachable for the first time; these queries still filtered
on hand-written ('pending', 'active'[, 'running']) lists, so a job waiting on
a dependency was invisible to duplicate detection (a re-asked task created a
second job), to the similar-title gate, to the narrative's existing-job loads
and to "what jobs do I have". COOLING had the same gap. All four now read the
single tuple in job_schema. The `_mysql` twin runs the same reads on MySQL.
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import LIVE_JOB_STATUSES, JobStatus

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'
AGENT = "agent_1"
USER = "user_1"
NARRATIVE = "nar_1"

VISIBLE = [s.value for s in LIVE_JOB_STATUSES]
HIDDEN = [s.value for s in JobStatus if s not in LIVE_JOB_STATUSES]


async def _seed(db, job_id, status, *, title="Daily report", narrative_id=NARRATIVE):
    await db.insert("instance_jobs", {
        "job_id": job_id, "instance_id": f"ins_{job_id}", "agent_id": AGENT,
        "user_id": USER, "title": title, "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": status, "notification_method": "inbox",
        "narrative_id": narrative_id, "next_run_time": "2030-01-01T00:00:00Z",
    })


def test_live_statuses_include_blocked_and_cooling_but_no_paused_or_terminal():
    assert JobStatus.BLOCKED in LIVE_JOB_STATUSES
    assert JobStatus.COOLING in LIVE_JOB_STATUSES
    for s in (JobStatus.PAUSED, JobStatus.PAUSED_NO_QUOTA, JobStatus.PAUSED_SPEND_CAP,
              JobStatus.BLOCKED_FAILED, JobStatus.COMPLETED, JobStatus.FAILED,
              JobStatus.CANCELLED):
        assert s not in LIVE_JOB_STATUSES


@pytest.mark.parametrize("status", VISIBLE)
@pytest.mark.asyncio
async def test_find_active_by_title_sees_every_live_status(db_client, status):
    await _seed(db_client, f"job_{status}", status)
    found = await JobRepository(db_client).find_active_by_title(AGENT, USER, "Daily report")
    assert found is not None and found.job_id == f"job_{status}"


@pytest.mark.parametrize("status", HIDDEN)
@pytest.mark.asyncio
async def test_find_active_by_title_ignores_paused_and_terminal(db_client, status):
    await _seed(db_client, f"job_{status}", status)
    assert await JobRepository(db_client).find_active_by_title(AGENT, USER, "Daily report") is None


@pytest.mark.asyncio
async def test_the_four_reads_agree_on_the_live_set(db_client):
    for status in VISIBLE + HIDDEN:
        await _seed(db_client, f"job_{status}", status, title=f"t_{status}")
    repo = JobRepository(db_client)

    by_narrative = {j.job_id for j in await repo.get_active_jobs_by_narrative(NARRATIVE)}
    by_agent = {j.job_id for j in await repo.get_active_jobs_by_agent(AGENT, user_id=USER)}
    summary = {r["job_id"] for r in await repo.get_active_jobs_summary(AGENT, USER, limit=50)}

    expected = {f"job_{s}" for s in VISIBLE}
    assert by_narrative == expected
    assert by_agent == expected
    assert summary == expected


@pytest.mark.asyncio
async def test_due_poll_is_untouched_by_the_live_set(db_client):
    """get_due_jobs must stay PENDING/ACTIVE — selecting a BLOCKED job there
    would undo the B-16 fix itself."""
    await _seed(db_client, "job_blocked_due", "blocked")
    await db_client.update("instance_jobs", {"job_id": "job_blocked_due"},
                           {"next_run_time": "2020-01-01T00:00:00Z"})
    await _seed(db_client, "job_active_due", "active")
    await db_client.update("instance_jobs", {"job_id": "job_active_due"},
                           {"next_run_time": "2020-01-01T00:00:00Z"})

    due = {j.job_id for j in await JobRepository(db_client).get_due_jobs()}
    assert due == {"job_active_due"}
