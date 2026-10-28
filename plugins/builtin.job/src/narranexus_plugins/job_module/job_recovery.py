"""
@file_name: job_recovery.py
@author: Bin Liang
@date: 2026-06-01
@description: Edge-triggered recovery of a single user's PAUSED_NO_QUOTA jobs.

PAUSED_NO_QUOTA is EVENT-recovered, not time-recovered: the blocker (no usable
provider) only clears when the user/admin acts — tops up quota, configures an
own provider, disables the free-tier toggle, or logs in. So instead of scanning
every poll cycle (the oscillation source), the backend routes that perform those
mutations call `rearm_user_no_quota_jobs(user_id, db)` after committing. It runs
a live readiness check and flips that user's paused jobs back to ACTIVE only if
ready. Cross-process safe: it writes job.status (the single authority) directly,
and the jobs poller picks the re-armed jobs up on its next cycle.

This is the PRIMARY recovery path; JobTrigger keeps a low-frequency scan as a
backstop for missed edges.

Also home of the user-initiated pause / resume / reschedule operations and of
`resume_jobs_paused_for_principal`, the job half of an admin account reinstate.
"""
from __future__ import annotations

import asyncio
from typing import Iterable

from loguru import logger
from pydantic import ValidationError

from narranexus.platform.repository import InstanceRepository, JobRepository
from narranexus.platform.schema.instance_schema import InstanceStatus
from narranexus.platform.schema.job_schema import JobStatus, JobType, TriggerConfig
from narranexus.platform.agent_framework.providers.readiness import ProviderReadiness
from narranexus.platform.utils.job_scheduling import compute_next_run, past_schedule_horizon
from narranexus.platform.utils import utc_now


async def rearm_user_no_quota_jobs(user_id: str, db) -> int:
    """Re-arm `user_id`'s PAUSED_NO_QUOTA jobs to ACTIVE iff the user is now
    provider-ready (live check). Returns the count re-armed. Best-effort: never
    raises into the calling route.

    `user_id` matches both the job owner (`user_id`) and the execution principal
    (`related_entity_id`), since a quota/provider change for a user should revive
    jobs that run *as* that user.
    """
    try:
        repo = JobRepository(db)
        paused = await repo.get_jobs_by_status(JobStatus.PAUSED_NO_QUOTA)
        mine = [
            j for j in paused
            if (j.related_entity_id or j.user_id) == user_id
        ]
        if not mine:
            return 0

        ready, reason = await ProviderReadiness.validate(user_id, db)
        if not ready:
            logger.debug(
                f"rearm_user_no_quota_jobs: {user_id} still not ready ({reason}), "
                f"leaving {len(mine)} job(s) paused"
            )
            return 0

        rearmed = 0
        for job in mine:
            next_run = compute_next_run(
                job_type=job.job_type,
                trigger_config=job.trigger_config,
                last_run_utc=utc_now(),
            )
            if next_run:
                await repo.update_next_run(job.job_id, next_run)
            await repo.update_job(job.job_id, {
                "status": JobStatus.ACTIVE.value,
                "paused_reason": None,
            })
            rearmed += 1
        logger.info(
            f"Edge re-arm: {rearmed} PAUSED_NO_QUOTA job(s) for {user_id} "
            f"→ ACTIVE (ready: {reason})"
        )
        return rearmed
    except Exception as e:  # noqa: BLE001 — recovery must never break the caller
        logger.warning(f"rearm_user_no_quota_jobs failed for {user_id}: {e}")
        return 0


_RESUMABLE_STATUSES = (
    JobStatus.PAUSED, JobStatus.PAUSED_NO_QUOTA, JobStatus.PAUSED_SPEND_CAP,
    JobStatus.COOLING, JobStatus.BLOCKED_FAILED,
)


async def pause_job(job_id: str, db) -> tuple[bool, str]:
    """User-initiated pause. Returns (ok, detail). A `paused` job stays put —
    excluded from the due-poll AND the auto-resume/cooling re-arm scans. Terminal
    jobs (completed/cancelled/failed) can't be paused. Portable (repository) — no
    backend-specific SQL."""
    repo = JobRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        return False, "job not found"
    if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED):
        return False, f"cannot pause from status={job.status.value}"
    await repo.update_job(job_id, {
        "status": JobStatus.PAUSED.value,
        "paused_reason": "user",
        "paused_at": utc_now(),
    })
    return True, job.status.value


async def resume_job(job_id: str, db) -> tuple[bool, str]:
    """Resume a paused / no-quota / spend-capped / cooling /
    dependency-blocked-failed job: recompute next_run from now, clear
    backoff/pause state, flip to ACTIVE. If the underlying blocker is still
    unresolved the next run simply re-pauses (a spend-capped job re-pauses
    at its next start until the user's local day rolls over)."""
    repo = JobRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        return False, "job not found"
    if job.status not in _RESUMABLE_STATUSES:
        return False, f"cannot resume from status={job.status.value}"
    next_run = compute_next_run(
        job_type=job.job_type,
        trigger_config=job.trigger_config,
        last_run_utc=utc_now(),
    )
    if next_run:
        await repo.update_next_run(job_id, next_run)
    await repo.update_job(job_id, {
        "status": JobStatus.ACTIVE.value,
        "paused_reason": None,
        "cooldown_until": None,
        "consecutive_failure_count": 0,
    })
    return True, job.status.value


async def resume_jobs_paused_for_principal(
    db, user_id: str, paused_reasons: Iterable[str]
) -> int:
    """Undo an account suspension's job pause (review r2 I-B): resume every
    job that executes as `user_id` and is `paused` with a `paused_reason` in
    `paused_reasons` (the reasons a suspension writes). Returns how many were
    brought back to ACTIVE. Exposed as the ``jobs.resume_for_principal``
    service; `POST /api/admin/reinstate` calls it after the account flip.

    Selection is the mirror of `JobRepository.pause_jobs_for_execution_principal`
    (same execution-principal predicate), pinned to BOTH status='paused' and
    the reason, so a job the user paused themselves stays paused.

    Each job goes through the ordinary job-layer resume, never a blind status
    flip: a recurring job whose next fire (computed from NOW, so the fires
    missed while suspended are not replayed) would land past its `end_at`
    horizon is COMPLETED instead — `next_run_time` cleared and its module
    instance marked completed, as every other re-arm path does — and every
    other job goes through `resume_job` (recompute next_run from now, clear
    pause/backoff state, ACTIVE). Completed-by-horizon jobs are not counted.

    Raises on a repository error; the caller owns the best-effort policy.
    """
    repo = JobRepository(db)
    jobs = await repo.get_jobs_paused_for_execution_principal(user_id, paused_reasons)
    resumed = 0
    for job in jobs:
        next_run = compute_next_run(
            job_type=job.job_type,
            trigger_config=job.trigger_config,
            last_run_utc=utc_now(),
        )
        if next_run and job.job_type != JobType.ONE_OFF and past_schedule_horizon(
            job.trigger_config, next_run.utc
        ):
            await repo.update_job(job.job_id, {
                "status": JobStatus.COMPLETED.value,
                "paused_reason": None,
                "paused_at": None,
            })
            await repo.clear_next_run(job.job_id)
            if job.instance_id:
                await InstanceRepository(db).update_status(
                    job.instance_id, InstanceStatus.COMPLETED, completed_at=utc_now(),
                )
            logger.info(
                f"Job {job.job_id} completed instead of resumed "
                f"(reinstate of {user_id}: next fire past end_at horizon)"
            )
            continue
        ok, _ = await resume_job(job.job_id, db)
        if ok:
            resumed += 1
    if jobs:
        logger.info(
            f"Reinstate: resumed {resumed} of {len(jobs)} suspension-paused job(s) for {user_id}"
        )
    return resumed


# A reschedule may touch any editable job EXCEPT one that is mid-execution
# (running) or already terminal (completed/cancelled/failed). Everything else —
# active / pending / paused / paused_no_quota / paused_spend_cap / cooling /
# blocked / blocked_failed — is a legitimate edit target; the status is left untouched (a paused job stays
# paused, and its later resume re-derives next_run from the new rule anyway).
_NON_EDITABLE_STATUSES = (
    JobStatus.RUNNING,
    JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED,
)
# The fields a reschedule may touch: the time-bearing trigger fields (derived
# from TriggerConfig.TIME_BEARING_FIELDS — one list, review I11) plus
# `timezone`, which is not time-bearing itself but travels with them. `end_at`
# is deliberately left out: it is the schedule's HORIZON ("runs until date
# X"), not its fire rule, has no editor in the UI (RescheduleBody), and the
# Jobs detail mirror documents that it is not reschedule-editable. The
# ONGOING semantics fields (end_condition / max_iterations) are out of scope.
_RESCHEDULE_FIELDS = tuple(
    f for f in TriggerConfig.TIME_BEARING_FIELDS if f != "end_at"
) + ("timezone",)


async def reschedule_job(job_id: str, new_fields: dict, db) -> tuple[bool, str]:
    """User-initiated reschedule ("edit execution time").

    Merges the provided time fields into the job's existing trigger_config,
    revalidates through TriggerConfig (naive run_at / IANA timezone / tz-required
    / interval clamp), recomputes next_run from now, and persists the new
    trigger_config followed by next_run (two writes: update_job_fields, then the
    mandatory update_next_run for the alpha+beta pair — NOT a single transaction).
    The job's status is intentionally left unchanged.

    Portable (repository only; no backend-specific SQL). Callers keep auth.

    Args:
        job_id: target job.
        new_fields: subset of {run_at, cron, interval_seconds, timezone}. A key
            present with value None clears that field (defense-in-depth; the
            route strips None so the UI never nulls a field).
        db: AsyncDatabaseClient.

    Returns:
        (ok, detail). detail is "ok" on success, else a human-readable reason.
    """
    repo = JobRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        return False, "job not found"
    if job.status in _NON_EDITABLE_STATUSES:
        return False, f"cannot reschedule from status={job.status.value}"

    merged = job.trigger_config.model_dump()
    for k in _RESCHEDULE_FIELDS:
        if k in new_fields:
            merged[k] = new_fields[k]

    # cron and interval_seconds are mutually exclusive triggering modes for a
    # scheduled/ongoing job. Switching between them (e.g. interval → cron) must
    # clear the sibling, or the stale field lingers in trigger_config and future
    # edits/reads get ambiguous input. compute_next_run prefers cron, so a
    # lingering interval would also be silently shadowed. Whichever mode the
    # caller just set wins; the other is cleared.
    if new_fields.get("cron"):
        merged["interval_seconds"] = None
    elif new_fields.get("interval_seconds"):
        merged["cron"] = None

    try:
        new_cfg = TriggerConfig(**merged)
    except ValidationError as e:
        return False, f"invalid schedule: {e}"

    # Guard: the job must still carry a fireable time field for its type, or it
    # would silently never fire again.
    if job.job_type == JobType.ONE_OFF and new_cfg.run_at is None:
        return False, "one_off job requires run_at"
    if job.job_type in (JobType.SCHEDULED, JobType.ONGOING) \
            and not new_cfg.cron and not new_cfg.interval_seconds:
        return False, "scheduled job requires cron or interval_seconds"

    next_run = compute_next_run(
        job_type=job.job_type,
        trigger_config=new_cfg,
        last_run_utc=utc_now(),
    )
    await repo.update_job_fields(job_id, {"trigger_config": new_cfg})
    if next_run:
        await repo.update_next_run(job_id, next_run)
    return True, "ok"


# Keep references to in-flight background tasks so they aren't garbage-collected
# mid-run (incident lesson #2: an un-referenced create_task can be reclaimed).
_bg_rearm_tasks: set = set()


def schedule_user_no_quota_rearm(user_id: str) -> None:
    """Fire-and-forget edge re-arm — called from backend mutation routes
    (login / quota grant / preference toggle / provider save). Non-blocking so
    it never adds latency to the user's request (e.g. login returns immediately;
    the re-arm runs in the background and the jobs poller picks the revived jobs
    up next cycle). Uses the global db client, not a request-scoped one, since
    the task outlives the request.
    """
    if not user_id:
        return

    async def _run():
        try:
            from narranexus.platform.utils import get_db_client
            db = await get_db_client()
            await rearm_user_no_quota_jobs(user_id, db)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"schedule_user_no_quota_rearm failed for {user_id}: {e}")

    try:
        task = asyncio.create_task(_run())
        _bg_rearm_tasks.add(task)
        task.add_done_callback(_bg_rearm_tasks.discard)
    except RuntimeError:
        # No running loop (e.g. called from sync context) — skip; the poller
        # backstop will still recover the jobs eventually.
        logger.debug(f"schedule_user_no_quota_rearm: no running loop for {user_id}")
