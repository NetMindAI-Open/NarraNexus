"""
@file_name: run_once.py
@author: Bin Liang
@date: 2026-09-04
@description: Execute one stored job on demand through JobTrigger's own execution body, then drain other due jobs briefly.

Moved out of backend/routes/manyfold/sync.py (batch 3c.6): the Manyfold
"run this job now" alarm needs JobTrigger's private execution machinery
(try_acquire_job CAS, prompt build, run, finalize — identical side effects to
a poller pickup) plus the maintenance passes the poller would otherwise run
(COOLING re-arm, PAUSED_NO_QUOTA backstop). That is builtin.job's business,
exposed as the ``jobs.run_once`` service; the route only streams the outcome.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from narranexus.contracts.job import JobRunOutcome
from narranexus.platform.utils.db.db_factory import get_db_client

_DRAIN_LIMIT = 5
_DRAIN_WINDOW_S = 30
_DRAIN_BUDGET_S = 300
_DRAIN_POLL_INTERVAL_S = 5

_TERMINAL_JOB_STATUSES = {"completed", "cancelled", "failed"}
_RUNNABLE_STATUSES = {"pending", "active"}


def _status_str(job: Any) -> str:
    status = getattr(job, "status", "")
    value = getattr(status, "value", status)
    return str(value or "").lower()


async def run_job_once(agent_id: str, job_id: str) -> JobRunOutcome:
    """Run ``job_id`` for ``agent_id`` now; never raises (the caller streams the outcome)."""
    try:
        return await _run_job_once_inner(agent_id, job_id)
    except Exception as e:  # noqa: BLE001 — the outcome is the error channel
        logger.exception(f"run_job {job_id} failed: {e}")
        return JobRunOutcome(job_id=job_id, ok=False, reason="internal_error")


async def _run_job_once_inner(agent_id: str, job_id: str) -> JobRunOutcome:
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.repository.job_repository import JobRepository

    db = await get_db_client()
    trigger = JobTrigger(database_client=db)
    repo = JobRepository(db)
    await trigger._rearm_cooled_jobs()
    await trigger._resume_eligible_no_quota_jobs()
    job = await repo.get_job(job_id)
    if job is None:
        return JobRunOutcome(job_id=job_id, ok=False, reason="not_found")
    if job.agent_id != agent_id:
        return JobRunOutcome(job_id=job_id, ok=False, reason="wrong_agent")
    status = _status_str(job)
    if status == "running":
        return JobRunOutcome(job_id=job_id, ok=False, reason="already_running")
    if status in _TERMINAL_JOB_STATUSES:
        return JobRunOutcome(job_id=job_id, ok=False, reason="terminal")
    if status not in _RUNNABLE_STATUSES:
        return JobRunOutcome(job_id=job_id, ok=False, reason=f"status_{status}")
    await trigger._execute_job(job)
    executed = {job_id}
    drained = await _drain_due_jobs(trigger, repo, executed)
    final = await repo.get_job(job_id)
    return JobRunOutcome(
        job_id=job_id,
        ok=True,
        status=_status_str(final) if final else "unknown",
        drained=drained,
    )


async def _drain_due_jobs(trigger: Any, repo: Any, executed: set[str]) -> int:
    """Sequentially pick up jobs that became due while we are awake —
    dependency chains activated by module_poller (Path B) and any due job
    Manyfold has not mirrored yet. Bounded by count and by budget so one
    dispatch cannot turn into an unbounded background poller."""
    drained = 0
    loop = asyncio.get_event_loop()
    window_ends = loop.time() + _DRAIN_WINDOW_S
    budget_ends = loop.time() + _DRAIN_BUDGET_S
    while drained < _DRAIN_LIMIT and loop.time() < window_ends and loop.time() < budget_ends:
        due = await repo.get_due_jobs(limit=_DRAIN_LIMIT * 2)
        fresh = [j for j in due if j.job_id not in executed]
        if not fresh:
            await asyncio.sleep(_DRAIN_POLL_INTERVAL_S)
            continue
        for job in fresh:
            if drained >= _DRAIN_LIMIT or loop.time() >= budget_ends:
                break
            executed.add(job.job_id)
            try:
                await trigger._execute_job(job)
                drained += 1
                # Executing a job may unblock dependents — extend the
                # window so the freshly activated chain link is caught.
                window_ends = loop.time() + _DRAIN_WINDOW_S
            except Exception as e:  # noqa: BLE001 — one job must not stop the drain
                logger.exception(f"drain: job {job.job_id} failed: {e}")
    return drained


__all__ = ["run_job_once"]
