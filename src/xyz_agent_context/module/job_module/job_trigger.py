"""
Job Trigger - Background Task Executor

@file_name: job_trigger.py
@author: NetMind.AI
@date: 2025-11-25
@updated: 2026-01-15 (Feature 3.1 - Context Loading Enhancement)
@description: Background polling service for job execution

=============================================================================
Overview
=============================================================================

JobTrigger is a background service that:
1. Polls the database for jobs that are due for execution
2. Builds execution prompts with enriched context (Feature 3.1) and calls AgentRuntime
3. Writes results to user's Inbox via ChatModule
4. Updates job status and execution records

Feature 3.1 Enhancement (2026-01-15):
- Loads Social Network context (related entities information)
- Loads Narrative Summary (overall progress, includes conversation history summary)
- Loads Dependency Outputs (existing feature, maintained)

Execution Flow:
    ┌─────────────────────────────────────────────────────────────────────┐
    │                        JobTrigger Loop                               │
    │                                                                      │
    │   1. Poll DB for due jobs (next_run_time <= now, status = PENDING/ACTIVE)
    │   2. For each job:                                                   │
    │      a. Update status to RUNNING                                     │
    │      b. Build execution prompt from job payload                      │
    │      c. Call AgentRuntime.run()                                      │
    │      d. Write result to Inbox                                        │
    │      e. Update job status and next_run_time                          │
    │   3. Sleep for poll_interval seconds                                 │
    │   4. Repeat                                                          │
    └─────────────────────────────────────────────────────────────────────┘

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │                         ModuleRunner                                 │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
    │  │  A2A API    │  │  MCP        │  │  MCP        │  │  Job       │ │
    │  │  Server     │  │  Modules    │  │  Job Module │  │  Trigger   │ │
    │  └─────────────┘  └─────────────┘  └─────────────┘  └─────┬──────┘ │
    └───────────────────────────────────────────────────────────┼────────┘
                                                                │
                                                                ▼
                                                    ┌─────────────────┐
                                                    │  AgentRuntime   │
                                                    └─────────────────┘

Usage:
    # Standalone
    uv run python -m xyz_agent_context.module.job_module.job_trigger

    # With custom interval
    uv run python -m xyz_agent_context.module.job_module.job_trigger --interval 30
"""

import asyncio
import argparse
from typing import List, Optional, Dict, Any, Set
from uuid import uuid4

from loguru import logger

# Schema
from xyz_agent_context.schema.job_schema import (
    JobModel,
    JobStatus,
    JobType,
    TriggerConfig,
)
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.runtime_message import AUTH_EXPIRED_ERROR_TYPE
from xyz_agent_context.agent_runtime.client import get_agent_runtime_client

# Utils
from xyz_agent_context.utils import DatabaseClient, get_db_client, utc_now, format_for_llm

# Repository
from xyz_agent_context.repository import JobRepository
from xyz_agent_context.services.service_audit import ServiceAuditor
# Leaf shared util (the real-time circuit-breaker imports the same) — NOT a
# cross-module dependency (binding rule #3). Reused so the Job layer recognises
# the SAME deterministic, won't-heal-by-waiting failures (#110) that the
# real-time layer already treats as self-serviceable.
from xyz_agent_context.agent_framework.llm_failure import (
    classify_self_serviceable,
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE,
    SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND,
)
from xyz_agent_context.module.job_module._job_scheduling import compute_next_run
from zoneinfo import ZoneInfo
from datetime import timedelta, timezone

# Context builder (extracted: dependency outputs, social network, narrative, prompt assembly)
from xyz_agent_context.module.job_module._job_context_builder import build_execution_prompt


# error_type values (set by step_3_agent_loop's `error_type = type(e).__name__`)
# that mean "this run cannot succeed until the owner tops up quota or configures
# their own provider". On these we PAUSE the job (PAUSED_NO_QUOTA) instead of
# rescheduling — otherwise a recurring/ongoing job re-fires every interval into
# the same wall (the infinite-loop bug, #6). Transient errors (network / LLM
# hiccups) are deliberately NOT here: those still reschedule so the next
# interval can succeed. (铁律 #14: this is job-scheduler-level, never an
# agent_loop limit. #15: own-provider users' runs succeed and never hit this.)
_NO_QUOTA_ERROR_TYPES: frozenset = frozenset({
    "QuotaExceededError",         # free tier exhausted, no own provider
    "FreeTierExhaustedError",     # free tier exhausted, has own provider (toggle)
    "NoProviderConfiguredError",  # opted out, no own provider
    "SystemDefaultUnavailable",   # free tier disabled / quota gone (api_config path)
    "LLMConfigNotConfigured",     # provider_driver resolution: no usable config
})

# Fallback substrings for the generic-except path that only carries a message.
_NO_QUOTA_ERROR_MARKERS = (
    "quota exhausted",
    "free quota exhausted",
    "free-tier quota exhausted",
    "no provider configured",
    "system free tier",
)


# paused_reason values whose fix the TIME-BASED backstop CANNOT observe. A
# balance top-up leaves the config unchanged (and we can't pre-check balance —
# no login JWT stored); a too-small context window or bad model id only clears
# when the user changes the slot config. So `_resume_eligible_no_quota_jobs`
# (which gates on static config-completeness, not a live test) must NOT
# blind-probe these — re-arming them every cycle IS the retry storm. They resume
# only on a real edge: `rearm_user_no_quota_jobs` (login / provider save), whose
# LIVE provider test actually observes whether the condition cleared, or a manual
# action. Auth / legacy-quota pauses are NOT here — reconfiguring a key changes
# config, which the static readiness check DOES observe.
_EDGE_ONLY_RESUME_REASONS: frozenset[str] = frozenset({
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE,
    SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND,
})


def _is_no_quota_failure(result: Dict[str, Any]) -> bool:
    """True when a failed job result won't fix itself by waiting — pause
    (PAUSED_NO_QUOTA) instead of rescheduling.

    Covers three groups:
    1. quota/provider-config exhaustion by error TYPE (`_NO_QUOTA_ERROR_TYPES`);
    2. any deterministic SELF-SERVICEABLE failure — insufficient balance/quota,
       context window too small, model-not-found (#110's `classify_self_serviceable`,
       reused so both layers agree). For a BACKGROUND job there is no interactive
       user to read a per-turn message, so the only way to stop the retry storm
       (9 users / 14 days / 390 retries, upstream incident) is to PAUSE — it
       auto-resumes via the existing edge + 15-min backstop once the owner tops
       up / reconfigures;
    3. legacy message-substring quota markers (`_NO_QUOTA_ERROR_MARKERS`).
    """
    if result.get("success", True):
        return False
    et = result.get("error_type")
    if et and et in _NO_QUOTA_ERROR_TYPES:
        return True
    if classify_self_serviceable(et, result.get("error")) is not None:
        return True
    msg = (result.get("error") or "").lower()
    return any(m in msg for m in _NO_QUOTA_ERROR_MARKERS)


# Auth/credential failures are ALSO "the run cannot succeed until the OWNER acts"
# (re-login / refresh OAuth / fix the API key) — recoverable, NOT transient.
# Treating them as transient escalated them to terminal FAILED after
# _MAX_CONSECUTIVE_FAILURES, and FAILED has no recovery path, so a job stayed
# dead even after the owner fixed auth (incident 2026-07-13). Route them to the
# same PAUSED_NO_QUOTA "provider/credentials unusable" pause, which the readiness
# backstop revives — never to terminal FAILED.
_AUTH_FAILURE_ERROR_TYPES: frozenset = frozenset({
    AUTH_EXPIRED_ERROR_TYPE,       # "auth_expired" — the agent_loop's explicit tag
    "AuthenticationError",
    "authentication_error",
    "invalid_api_key",
    "PermissionDeniedError",
})
_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "check your api key",
    "invalid api key",
    "not logged in",
    "expired token",
    "unauthorized",
    "401",
)


def _is_auth_failure(result: Dict[str, Any]) -> bool:
    """True when a failed run is an auth/credential problem — recoverable once
    the owner re-authenticates, so it must PAUSE (like no-quota), never escalate
    to the terminal FAILED state (which has no recovery path)."""
    if result.get("success", True):
        return False
    et = result.get("error_type")
    if et and et in _AUTH_FAILURE_ERROR_TYPES:
        return True
    msg = (result.get("error") or "").lower()
    return any(m in msg for m in _AUTH_FAILURE_MARKERS)


# Transient-failure backoff (batch ②, 2026-06-01). A non-quota run failure
# (network / 5xx / timeout / anything not in _NO_QUOTA_*) is treated as
# transient: the job goes to COOLING with an exponentially growing cooldown and
# retries when it elapses, instead of immediately rescheduling (which let a
# persistently-failing job spin every interval). After _MAX_CONSECUTIVE_FAILURES
# in a row it escalates to FAILED rather than cooling forever. A success resets
# the counter. (铁律 #14: this spaces SCHEDULER retries, never caps a running
# agent_loop — only a run that finished AND failed accrues backoff.)
_BACKOFF_BASE_SECONDS = 60
_BACKOFF_CAP_SECONDS = 3600
_MAX_CONSECUTIVE_FAILURES = 8

# PAUSED_NO_QUOTA recovery is edge-triggered (login / quota grant / preference
# toggle / provider save call rearm_user_no_quota_jobs). The poll scan is only a
# backstop for missed edges, so it runs at this low interval instead of every
# 60s cycle (high-frequency scanning was the oscillation amplifier).
_NO_QUOTA_BACKSTOP_INTERVAL_S = 900  # 15 minutes


def _compute_cooldown_seconds(consecutive_failures: int) -> int:
    """Exponential backoff: base · 2^(n-1), clamped to the cap.
    n=1→60s, 2→120s, 3→240s, … capped at 3600s (1h)."""
    n = max(1, consecutive_failures)
    return min(_BACKOFF_BASE_SECONDS * (2 ** (n - 1)), _BACKOFF_CAP_SECONDS)


class JobTrigger:
    """
    Job Trigger - Background Polling Service

    Core responsibilities:
    1. Periodically poll the database to find jobs due for execution
    2. Build execution Prompt and call AgentRuntime
    3. Process execution results and write to Inbox
    4. Update Job status and next execution time

    Lifecycle:
    1. ModuleRunner creates JobTrigger instance
    2. Calls start() in an independent process to begin polling
    3. Calls stop() for graceful shutdown when receiving termination signal
    """

    # =========================================================================
    # Initialization
    # =========================================================================

    def __init__(
        self,
        poll_interval: int = 60,
        job_timeout_minutes: int = 30,
        max_workers: int = 5,
        database_client: Optional[DatabaseClient] = None
    ):
        """
        Initialize JobTrigger

        Args:
            poll_interval: Polling interval (seconds), default 60 seconds
            job_timeout_minutes: Job timeout (minutes), default 30 minutes
            max_workers: Maximum concurrent worker count, default 5
            database_client: Database client (optional, lazy-loaded if not provided)
        """
        self.poll_interval = poll_interval
        self.job_timeout_minutes = job_timeout_minutes
        self.max_workers = max_workers
        self._db = database_client  # May be None, lazy-loaded
        self.running = False

        # Low-frequency backstop bookkeeping: the PAUSED_NO_QUOTA resume scan is
        # only a safety net now (primary recovery is edge-triggered from the
        # provider-mutation routes). Running it every poll cycle was wasteful and
        # was the 2026-05-31 oscillation amplifier. Gate it to a slow interval.
        self._last_no_quota_backstop = None

        # Repository (lazy initialization)
        self._job_repo: Optional[JobRepository] = None

        # Worker Pool related
        self._job_queue: asyncio.Queue[JobModel] = asyncio.Queue()
        self._running_jobs: Set[str] = set()  # Set of currently executing job_ids, prevents duplicate enqueue
        self._workers: List[asyncio.Task] = []
        self._poller_task: Optional[asyncio.Task] = None

        # L2 observability — see services/service_audit.py. The cumulative
        # enqueue counter rides the heartbeat detail so a frozen count in a
        # stale heartbeat exposes a wedged poll loop (incident lesson #4).
        self.audit = ServiceAuditor("job_trigger")
        self._enqueued_total = 0

        logger.info(
            f"JobTrigger initialized: poll_interval={poll_interval}s, "
            f"timeout={job_timeout_minutes}min, max_workers={max_workers}"
        )

    @property
    def db(self) -> DatabaseClient:
        """Get database client (must be used after start())"""
        if self._db is None:
            raise RuntimeError("Database client not initialized. Call start() first.")
        return self._db

    def _get_job_repo(self) -> JobRepository:
        """Get or create JobRepository instance"""
        if self._job_repo is None:
            self._job_repo = JobRepository(self.db)
        return self._job_repo

    # =========================================================================
    # Lifecycle Management
    # =========================================================================

    async def start(self) -> None:
        """
        Start JobTrigger (Worker Pool mode)

        Architecture:
        - 1 Poller coroutine: periodically queries tasks and puts them in queue
        - N Worker coroutines: takes tasks from queue and executes them

        This is JobTrigger's main entry point, runs continuously until stop() is called.
        """
        # Initialize database client in async context
        if self._db is None:
            self._db = await get_db_client()
            logger.info("Database client initialized in async context")

        # Ensure all tables exist (poller runs as separate process)
        from xyz_agent_context.utils.schema_registry import auto_migrate
        await auto_migrate(self._db._backend)
        logger.info("Schema auto-migration complete")

        # Initialise system-default quota so jobs run by agents whose
        # owners have no personal provider fall back to the free tier.
        from xyz_agent_context.agent_framework.quota_service import (
            bootstrap_quota_subsystem,
        )
        await bootstrap_quota_subsystem(self._db)

        logger.info("JobTrigger starting (Worker Pool mode)...")
        logger.info(f"   Poll interval: {self.poll_interval} seconds")
        logger.info(f"   Max workers: {self.max_workers}")
        logger.info(f"   Job timeout: {self.job_timeout_minutes} minutes")
        # Startup recovery: when new process starts, recover all running jobs to schedulable state
        # Because after old process was killed, execution of these jobs must have been interrupted
        repo = self._get_job_repo()
        recovered = await repo.recover_all_running_jobs()
        if recovered > 0:
            logger.warning(f"Startup recovery: recovered {recovered} stuck running jobs")

        self.running = True
        await self.audit.started({"poll_interval": self.poll_interval})

        # Start Workers
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
            logger.debug(f"Worker {i} started")

        # Start Poller
        self._poller_task = asyncio.create_task(self._poller())
        logger.debug("Poller started")

        # Wait for all tasks to complete (usually terminated by stop())
        try:
            await asyncio.gather(self._poller_task, *self._workers)
        except asyncio.CancelledError:
            logger.info("JobTrigger tasks cancelled")

        logger.info("JobTrigger stopped")

    async def stop(self) -> None:
        """
        Gracefully stop JobTrigger

        1. Set running=False to stop poller from enqueuing
        2. Wait for queued tasks to be processed
        3. Cancel all workers
        """
        logger.info("Stopping JobTrigger gracefully...")
        self.running = False
        await self.audit.stopped({"enqueued_total": self._enqueued_total})

        # Wait for queue to drain (max 30 seconds)
        try:
            await asyncio.wait_for(self._job_queue.join(), timeout=30)
            logger.info("All queued jobs completed")
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for queue to empty, forcing shutdown")

        # Cancel poller
        if self._poller_task:
            self._poller_task.cancel()

        # Cancel all workers
        for worker in self._workers:
            worker.cancel()

        # Wait for all task cancellations to complete
        await asyncio.gather(
            self._poller_task,
            *self._workers,
            return_exceptions=True
        )

        self._workers.clear()
        self._poller_task = None
        logger.info("JobTrigger shutdown complete")

    # =========================================================================
    # Worker Pool Core
    # =========================================================================

    async def _poller(self) -> None:
        """
        Poller coroutine: periodically queries tasks and puts them in queue

        Responsibilities:
        1. Recover stuck tasks
        2. Query due tasks
        3. Put tasks into queue (skip already executing ones)
        """
        while self.running:
            try:
                await self._poll_and_enqueue()
                # Throttled L2 heartbeat carrying the cumulative enqueue
                # counter — a stale row with a frozen count means the loop
                # wedged though the process is still alive (lesson #4).
                await self.audit.heartbeat({"enqueued_total": self._enqueued_total})
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                logger.debug("Poller cancelled")
                break
            except Exception as e:
                logger.exception(f"Poller error: {e}")
                await self.audit.error(str(e))
                await asyncio.sleep(self.poll_interval)

    async def _worker(self, worker_id: int) -> None:
        """
        Worker coroutine: takes tasks from queue and executes them

        Args:
            worker_id: Worker number (for logging)
        """
        logger.debug(f"Worker {worker_id} ready")

        while True:
            try:
                # Get task from queue (blocking wait)
                job = await self._job_queue.get()

                try:
                    logger.info(f"[Worker {worker_id}] Executing job: {job.job_id}")
                    await self._execute_job(job)
                finally:
                    # Mark task as done
                    self._job_queue.task_done()
                    # Remove from running set
                    self._running_jobs.discard(job.job_id)

            except asyncio.CancelledError:
                logger.debug(f"Worker {worker_id} cancelled")
                break
            except Exception as e:
                logger.exception(f"[Worker {worker_id}] Unexpected error: {e}")

    async def _poll_and_enqueue(self) -> None:
        """
        Execute one polling cycle and enqueue tasks

        1. Recover stuck tasks
        2. Query due tasks
        3. Put tasks into queue (skip already executing ones)
        """
        logger.debug(f"Polling for due jobs at {utc_now()}")

        try:
            repo = self._get_job_repo()

            # 1. Diagnose long-running jobs (铁律 #14: surface for alerting,
            # never force-recover a legitimate long agent_loop). Orphans left by
            # a killed process are recovered at startup (recover_all_running_jobs).
            await self._diagnose_long_running_jobs()

            # 1.5 Backstop only: scan PAUSED_NO_QUOTA at a low frequency to
            # catch edge signals that were missed. Primary recovery is
            # edge-triggered from the provider-mutation routes (see job_recovery).
            now_ts = utc_now()
            last = self._last_no_quota_backstop
            if last is None or (now_ts - last).total_seconds() >= _NO_QUOTA_BACKSTOP_INTERVAL_S:
                self._last_no_quota_backstop = now_ts
                await self._resume_eligible_no_quota_jobs()
                # Same cadence: self-heal "active but unschedulable" zombies —
                # an ACTIVE scheduled/ongoing job left with a NULL next_run_time
                # is never picked by get_due_jobs (NULL is never <= now), so it
                # silently never runs (incident 2026-07-13). Recompute its
                # next_run. Belt-and-suspenders behind the reactivation fix in
                # job_service.update_job.
                await self._heal_unscheduled_active_jobs()

            # 1.6 Re-arm COOLING jobs whose backoff cooldown has elapsed.
            await self._rearm_cooled_jobs()

            # 2. Query jobs due for execution
            due_jobs = await repo.get_due_jobs()

            if not due_jobs:
                logger.debug("No due jobs found")
                return

            # 3. Put tasks into queue (skip already executing ones)
            enqueued = 0
            for job in due_jobs:
                if job.job_id not in self._running_jobs:
                    self._running_jobs.add(job.job_id)
                    await self._job_queue.put(job)
                    self._enqueued_total += 1
                    enqueued += 1
                else:
                    logger.debug(f"Job {job.job_id} already running, skipped")

            if enqueued > 0:
                logger.info(f"Enqueued {enqueued} jobs (queue size: {self._job_queue.qsize()})")

        except Exception as e:
            logger.exception(f"Error in poll_and_enqueue: {e}")

    async def _user_can_run(self, user_id: str) -> bool:
        """Would a run for this user resolve a provider right now?

        Delegates to the single classifier (`ProviderResolver.classify` via
        `classify_provider_for_user`) so this resume gate can never drift from
        the runtime again — that drift was the root cause of the 2026-05-31
        pause/resume oscillation. The OLD implementation checked
        "quota.check() OR own-provider-complete", which IGNORED
        `prefer_system_override`: a user opted in to the (exhausted) free tier
        who also had an own provider was judged runnable, resumed, then
        rejected by the runtime (it will not silently spend their own key) —
        forever.

        铁律 #15 is still honoured: opted-out own-provider users pass via
        USER_OK; the platform never overrides the user's provider choice. It
        only stops resuming a job into a run the runtime will refuse.
        """
        try:
            from xyz_agent_context.agent_framework.provider_resolver import (
                classify_provider_for_user,
                is_runnable,
            )
            return is_runnable(await classify_provider_for_user(user_id, self.db))
        except Exception as e:  # noqa: BLE001 — quota/provider subsystem optional/unbootstrapped
            logger.debug(f"_user_can_run classify failed for {user_id}: {e}")
            return False

    async def _resume_eligible_no_quota_jobs(self) -> int:
        """Flip PAUSED_NO_QUOTA jobs back to ACTIVE when the owner can run again.
        Resume schedule goes forward (compute_next_run from now) so a long-paused
        recurring job doesn't fire a backlog burst on resume. (#6 resume path —
        covers both 'quota topped up' and 'user configured own provider'.)"""
        try:
            repo = self._get_job_repo()
            paused = await repo.get_jobs_by_status(JobStatus.PAUSED_NO_QUOTA)
            if not paused:
                return 0
            resumed = 0
            for job in paused:
                # Do NOT blind-probe reasons whose fix readiness can't observe
                # (balance top-up / model / context). Re-arming them every cycle
                # is exactly the retry storm — a balance-0 user reads as
                # config-complete → "can run" → re-arm → re-fail → forever. These
                # resume only on a real edge (rearm_user_no_quota_jobs) or manual.
                if job.paused_reason in _EDGE_ONLY_RESUME_REASONS:
                    continue
                exec_uid = job.related_entity_id or job.user_id
                if not exec_uid or not await self._user_can_run(exec_uid):
                    continue
                next_run = compute_next_run(
                    job_type=job.job_type,
                    trigger_config=job.trigger_config,
                    last_run_utc=utc_now(),
                )
                if next_run:
                    await repo.update_next_run(job.job_id, next_run)
                await repo.update_job_status(
                    job_id=job.job_id, status=JobStatus.ACTIVE
                )
                resumed += 1
                logger.info(
                    f"Job {job.job_id} resumed from PAUSED_NO_QUOTA (user={exec_uid})"
                )
            if resumed:
                logger.info(f"Resumed {resumed} job(s) from PAUSED_NO_QUOTA")
            return resumed
        except Exception as e:
            logger.exception(f"Error resuming PAUSED_NO_QUOTA jobs: {e}")
            return 0

    async def _rearm_cooled_jobs(self) -> int:
        """Flip COOLING jobs back to ACTIVE once their backoff cooldown has
        elapsed (time-based recovery — unlike PAUSED_NO_QUOTA, a cooling job's
        blocker is purely the clock, so polling is the natural trigger). The
        consecutive-failure count is preserved so a job that keeps failing keeps
        escalating its backoff and eventually hits the FAILED cap. next_run_time
        was set to cooldown_until when the job entered COOLING, so the re-armed
        job is immediately due."""
        try:
            repo = self._get_job_repo()
            cooling = await repo.get_jobs_by_status(JobStatus.COOLING)
            if not cooling:
                return 0
            now = utc_now()
            rearmed = 0
            for job in cooling:
                cu = job.cooldown_until
                if cu is not None:
                    # Normalize to aware-UTC so the comparison is correct
                    # whether the DB round-tripped a naive or aware datetime.
                    if cu.tzinfo is None:
                        cu = cu.replace(tzinfo=timezone.utc)
                    if cu > now:
                        continue  # still cooling
                await repo.update_job(job.job_id, {
                    "status": JobStatus.ACTIVE.value,
                    "cooldown_until": None,
                })
                rearmed += 1
                logger.info(f"Job {job.job_id} re-armed from COOLING (retry)")
            if rearmed:
                logger.info(f"Re-armed {rearmed} job(s) from COOLING")
            return rearmed
        except Exception as e:
            logger.exception(f"Error re-arming COOLING jobs: {e}")
            return 0

    async def _heal_unscheduled_active_jobs(self) -> int:
        """Self-heal 'active but unschedulable' zombies.

        An ACTIVE scheduled/ongoing job whose ``next_run_time`` is NULL will
        never be selected by ``get_due_jobs`` (its WHERE is ``next_run_time <=
        now``, and NULL is never <= now), so it looks active but silently never
        runs. This happened when a job was reactivated (status→active) without a
        fresh schedule after landing in a terminal/paused state (incident
        2026-07-13). Recompute next_run from the job's own trigger so the poller
        can pick it up. Returns the count healed."""
        try:
            repo = self._get_job_repo()
            stuck = await repo.get_active_scheduled_jobs_missing_next_run()
            if not stuck:
                return 0
            healed = 0
            for job in stuck:
                next_run = compute_next_run(
                    job_type=job.job_type,
                    trigger_config=job.trigger_config,
                    last_run_utc=utc_now(),
                )
                if next_run:
                    await repo.update_next_run(job.job_id, next_run)
                    healed += 1
                    logger.warning(
                        f"Job {job.job_id} self-healed: ACTIVE with NULL next_run "
                        f"→ rescheduled {next_run.local} ({next_run.tz})"
                    )
            if healed:
                logger.warning(f"Self-healed {healed} unscheduled ACTIVE job(s)")
            return healed
        except Exception as e:
            logger.exception(f"Error self-healing unscheduled ACTIVE jobs: {e}")
            return 0

    async def _diagnose_long_running_jobs(self) -> int:
        """Surface jobs that have been RUNNING longer than the timeout as a
        diagnostic WARNING — but DO NOT touch them (铁律 #14: long agent_loops
        are legitimate; force-recovering one would interrupt valid work and
        duplicate execution). Returns the number flagged. An operator can alert
        on these log lines; a genuinely orphaned RUNNING row (killed process) is
        reset at the next process start by recover_all_running_jobs."""
        try:
            repo = self._get_job_repo()
            long_running = await repo.find_long_running_jobs(
                threshold_minutes=self.job_timeout_minutes
            )
            for job in long_running:
                logger.warning(
                    f"[diagnostic] Job {job.job_id} has been RUNNING > "
                    f"{self.job_timeout_minutes}min (started_at={job.started_at}). "
                    f"Not force-recovering (铁律 #14) — verify the agent_loop is "
                    f"alive, not hung."
                )
            return len(long_running)
        except Exception as e:
            logger.exception(f"Error diagnosing long-running jobs: {e}")
            return 0

    # =========================================================================
    # Job Execution
    # =========================================================================

    async def _update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        error_message: Optional[str] = None
    ) -> int:
        """
        Update Job status

        Args:
            job_id: Job ID
            status: New status
            error_message: Error message (optional)

        Returns:
            Number of affected rows
        """
        return await self._get_job_repo().update_job_status(
            job_id=job_id,
            status=status,
            error_message=error_message
        )

    async def _update_instance_for_execution(self, instance_id: str) -> None:
        """
        Update Instance status to in_progress (for ModulePoller detection)

        Sets:
        - status = 'in_progress'
        - last_polled_status = 'in_progress'
        - callback_processed = False

        Args:
            instance_id: Instance ID
        """
        try:
            query = """
                UPDATE module_instances
                SET status = 'in_progress',
                    last_polled_status = 'in_progress',
                    callback_processed = FALSE,
                    updated_at = NOW()
                WHERE instance_id = %s
            """
            await self.db.execute(query, (instance_id,))
            logger.debug(f"Updated instance {instance_id} for execution (status=in_progress)")
        except Exception as e:
            logger.exception(f"Error updating instance {instance_id} for execution: {e}")

    async def _update_instance_completed(self, instance_id: str) -> None:
        """
        Update Instance status to completed (triggers ModulePoller detection)

        Sets:
        - status = 'completed'
        - completed_at = NOW()
        (Preserves last_polled_status = 'in_progress' for Poller change detection)

        Args:
            instance_id: Instance ID
        """
        try:
            query = """
                UPDATE module_instances
                SET status = 'completed',
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE instance_id = %s
            """
            await self.db.execute(query, (instance_id,))
            logger.debug(f"Updated instance {instance_id} to completed")
        except Exception as e:
            logger.exception(f"Error updating instance {instance_id} to completed: {e}")

    async def _update_instance_failed(self, instance_id: str) -> None:
        """
        Update Instance status to failed

        Args:
            instance_id: Instance ID
        """
        try:
            query = """
                UPDATE module_instances
                SET status = 'failed',
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE instance_id = %s
            """
            await self.db.execute(query, (instance_id,))
            logger.debug(f"Updated instance {instance_id} to failed")
        except Exception as e:
            logger.exception(f"Error updating instance {instance_id} to failed: {e}")

    async def _execute_job(self, job: JobModel) -> None:
        """
        Execute a single Job (Feature 3.1 Enhanced)

        Execution flow:
        1. Try to acquire execution lock (atomic operation, prevents duplicate execution)
        2. Build execution Prompt (with full context)
           - Social Network information (related_entity_id)
           - Narrative Summary (overall progress, includes conversation history summary)
           - Dependency Outputs (prerequisite task results)
        3. Call AgentRuntime (using related_entity_id as user_id)
        4. Process execution results
        5. Update Job status and next execution time

        Args:
            job: Job to execute
        """
        logger.info(f"Executing job: {job.job_id} - {job.title}")

        try:
            # 1. Try to atomically acquire execution lock (status: PENDING/ACTIVE -> RUNNING)
            # This prevents multiple Workers from executing the same Job simultaneously
            acquired = await self._get_job_repo().try_acquire_job(job.job_id)
            if not acquired:
                logger.warning(f"Failed to acquire lock for job {job.job_id}, skipping")
                return

            # 1.5 Update associated Instance status (for ModulePoller detection)
            if job.instance_id:
                await self._update_instance_for_execution(job.instance_id)

            # 2. Build execution Prompt (including dependency Job outputs)
            # Use job's own timezone (frozen at creation); do NOT read users.timezone
            # which may have changed since the job was scheduled.
            user_tz = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
            prompt = await build_execution_prompt(self.db, job, user_tz)
            logger.debug(f"Built prompt for job {job.job_id}: {prompt[:100]}...")

            # 3. Call AgentRuntime
            # Agent will send report to user via send_message_to_user_directly
            result = await self._run_agent(job, prompt)

            # 4. Update Job status
            await self._finalize_job_execution(job, result)

            logger.info(f"Job {job.job_id} executed successfully")

        except Exception as e:
            logger.exception(f"Error executing job {job.job_id}: {e}")
            await self._handle_job_failure(job, str(e))

    async def _run_agent(self, job: JobModel, prompt: str) -> Dict[str, Any]:
        """
        Execute job using AgentRuntime.

        Creates an AgentRuntime instance and runs the prompt,
        collecting all output. The agent sends the final report
        to the user via send_message_to_user_directly.

        Args:
            job: JobModel instance
            prompt: Execution prompt

        Returns:
            Dict containing event_id, content, and success status
        """
        event_id = f"event_{uuid4().hex[:12]}"

        try:
            logger.info(f"[JobTrigger] Starting AgentRuntime for job {job.job_id}")

            # File logging is now owned by setup_logging("job_trigger") at
            # process startup, not per-run by AgentRuntime. The fd leak
            # described in earlier comments — multiprocessing.SimpleQueue
            # backing each enqueue=True file sink, leaking 2-3 fd per
            # uncleaned run, saturating the EC2 jobs container at 1021/1024
            # fd in 3 days — is gone by construction: AgentRuntime no
            # longer adds/removes file handlers. Each line still carries
            # event_id / run_id via contextvars so per-run trace is
            # recoverable by `grep event_id=...` (M4 / T15).
            client = get_agent_runtime_client()

            # Execution identity: use related_entity_id (if available) as user_id, otherwise use job.user_id
            # This way Job executes in the target user's context, loading their Narrative and related info
            execution_user_id = job.related_entity_id or job.user_id
            logger.info(
                f"[JobTrigger] Executing job {job.job_id} as user_id={execution_user_id} "
                f"(related_entity_id={job.related_entity_id}, job.user_id={job.user_id})"
            )

            collection = await client.run_and_collect(
                agent_id=job.agent_id,
                user_id=execution_user_id,
                input_content=prompt,
                working_source=WorkingSource.JOB,
                job_instance_id=job.instance_id,
                forced_narrative_id=job.narrative_id,
                trigger_extra_data={"trigger_id": f"job_{job.job_id}"},
            )

            # Error path (Bug 2): previously the trigger swallowed the
            # ERROR message and surfaced a misleading "Task executed but
            # produced no text output" note. Now the job result carries
            # success=False + the structured error so the Job status
            # downstream (_finalize_job_execution / job.last_error) can
            # record it.
            if collection.is_error:
                logger.warning(
                    f"[JobTrigger] Job {job.job_id} failed: "
                    f"{collection.error.error_type}: {collection.error.error_message}"
                )
                return {
                    "event_id": event_id,
                    "content": (
                        f"⚠️ Scheduled task failed: {collection.error.error_message}"
                    ),
                    "success": False,
                    "error": collection.error.error_message,
                    "error_type": collection.error.error_type,
                    "tool_calls": collection.tool_calls,
                }

            content = collection.output_text
            tool_calls = collection.tool_calls

            # Add execution metadata if content is empty
            if not content.strip():
                # Use job's frozen timezone, not users.timezone
                user_tz = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
                executed_at_str = format_for_llm(utc_now(), user_tz)

                content = f"""## Task Completed: {job.title}

The task was executed but produced no text output.

**Execution Details:**
- Job ID: {job.job_id}
- Executed at: {executed_at_str}
- Tools used: {', '.join(tool_calls) if tool_calls else 'None'}

---
*This message was generated by a scheduled job.*
"""

            logger.info(f"[JobTrigger] AgentRuntime completed for job {job.job_id}, output length: {len(content)}")

            return {
                "event_id": event_id,
                "content": content,
                "success": True,
                "tool_calls": tool_calls,
            }

        except Exception as e:
            logger.exception(f"Error running agent for job {job.job_id}: {e}")

            return {
                "event_id": event_id,
                "content": f"Error executing job: {str(e)}",
                "success": False,
                "error": str(e),
            }

    # =========================================================================
    # Result Processing
    # =========================================================================

    async def _finalize_job_execution(
        self,
        job: JobModel,
        result: Dict[str, Any]
    ) -> None:
        """
        Finalize job after successful execution.

        Performs post-execution updates:
        1. Add event_id to process list
        2. Update last_run_time
        3. For one_off: mark as COMPLETED
        4. For scheduled: mark as ACTIVE and calculate next_run_time

        Args:
            job: JobModel instance
            result: Execution result
        """
        try:
            now = utc_now()
            event_id = result.get("event_id")
            repo = self._get_job_repo()

            # Add event to process list
            if event_id:
                await repo.add_event_to_process(
                    job_id=job.job_id,
                    event_id=event_id
                )

            # #6 — no-quota / no-provider pause. This runs for ALL job types
            # BEFORE the reschedule/complete branching: a run that failed
            # because the owner's free-tier quota is gone (and they have no own
            # provider) will fail identically every interval, so rescheduling
            # it just burns a runtime slot forever. Pause instead; the periodic
            # recheck (_resume_eligible_no_quota_jobs) flips it back to ACTIVE
            # when quota is restored or a provider is configured. Transient
            # failures fall through to the normal reschedule path below.
            if _is_no_quota_failure(result) or _is_auth_failure(result):
                # Record WHY we paused so the resume path can tell apart a fix
                # readiness can observe (auth/quota → config change) from one it
                # cannot (balance top-up / model / context — see
                # _EDGE_ONLY_RESUME_REASONS). Without this the backstop blind-
                # probes and re-arms every cycle into the same wall.
                ss_reason = classify_self_serviceable(
                    result.get("error_type"), result.get("error")
                )
                if ss_reason is not None:
                    pause_reason = ss_reason
                elif _is_auth_failure(result):
                    pause_reason = "auth"
                else:
                    pause_reason = "no_quota"
                await repo.update_job(job.job_id, {
                    "status": JobStatus.PAUSED_NO_QUOTA.value,
                    "paused_reason": pause_reason,
                    "paused_at": now,
                    "last_error": result.get("error"),
                })
                if job.instance_id:
                    await self._update_instance_failed(job.instance_id)
                logger.warning(
                    f"Job {job.job_id} paused (provider/credentials unusable, "
                    f"reason={pause_reason}): "
                    f"{result.get('error_type') or result.get('error')}"
                )
                return

            # Transient failure (not quota): exponential backoff via COOLING,
            # escalating to FAILED after the consecutive-failure cap. This
            # replaces the old "any failure reschedules straight to ACTIVE",
            # which let a persistently-failing job spin every interval.
            if not result.get("success", True):
                new_count = (job.consecutive_failure_count or 0) + 1
                err = result.get("error")
                if new_count >= _MAX_CONSECUTIVE_FAILURES:
                    await repo.update_job(job.job_id, {
                        "status": JobStatus.FAILED.value,
                        "consecutive_failure_count": new_count,
                        "paused_reason": "repeated_failure",
                        "paused_at": now,
                        "last_error": err,
                    })
                    await repo.clear_next_run(job.job_id)
                    if job.instance_id:
                        await self._update_instance_failed(job.instance_id)
                    logger.warning(
                        f"Job {job.job_id} FAILED after {new_count} consecutive "
                        f"transient failures: {err}"
                    )
                    return

                cooldown_secs = _compute_cooldown_seconds(new_count)
                retry_at = now + timedelta(seconds=cooldown_secs)
                tz_name = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
                retry_local = retry_at.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
                await repo.update_job(job.job_id, {
                    "status": JobStatus.COOLING.value,
                    "consecutive_failure_count": new_count,
                    "cooldown_until": retry_at,
                    "next_run_time": retry_at,
                    "next_run_at_local": retry_local,
                    "next_run_tz": tz_name,
                    "last_error": err,
                })
                # Do NOT touch instance status: a COOLING job has not reached a
                # terminal state, so dependents stay blocked until it finally
                # succeeds (completed) or gives up (failed).
                logger.warning(
                    f"Job {job.job_id} cooling (failure {new_count}/"
                    f"{_MAX_CONSECUTIVE_FAILURES}), retry at {retry_local} ({tz_name})"
                )
                return

            # Success: clear any prior transient-failure backoff state before the
            # normal complete/reschedule branching below.
            if (job.consecutive_failure_count or 0) > 0 or job.cooldown_until is not None:
                await repo.update_job(job.job_id, {
                    "consecutive_failure_count": 0,
                    "cooldown_until": None,
                    "paused_reason": None,
                })

            # Handle based on job type
            if job.job_type == JobType.ONE_OFF:
                # One-off job: mark completed, record last run, clear next run.
                # All three writes together honor the alpha+beta atomic invariant
                # (v2 timezone protocol).
                tz_name = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
                last_run_local = now.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
                await repo.update_last_run(job.job_id, now, last_run_local, tz_name)
                await repo.clear_next_run(job.job_id)
                await repo.update_job_status(
                    job_id=job.job_id,
                    status=JobStatus.COMPLETED
                )
                # Update Instance status to completed (triggers ModulePoller)
                if job.instance_id:
                    await self._update_instance_completed(job.instance_id)
                logger.info(f"Job {job.job_id} completed (one_off)")

            elif job.job_type == JobType.SCHEDULED:
                # Scheduled job: compute atomic alpha+beta triple and mark active
                tz_name = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
                last_run_local = now.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
                await repo.update_last_run(job.job_id, now, last_run_local, tz_name)

                next_run = compute_next_run(
                    job_type=job.job_type,
                    trigger_config=job.trigger_config,
                    last_run_utc=now,
                )
                if next_run:
                    await repo.update_next_run(job.job_id, next_run)
                else:
                    await repo.clear_next_run(job.job_id)

                await repo.update_job_status(
                    job_id=job.job_id,
                    status=JobStatus.ACTIVE
                )

                # Scheduled job also marked as completed after each execution (triggers ModulePoller)
                if job.instance_id:
                    await self._update_instance_completed(job.instance_id)

                next_run_str = next_run.local if next_run else "N/A"
                logger.info(f"Job {job.job_id} rescheduled, next run: {next_run_str} ({tz_name})")

            elif job.job_type == JobType.ONGOING:
                # ONGOING job: execute continuously until end_condition is met or max_iterations reached
                # Note: end_condition is primarily checked by hook_after_event_execution (entry point 1)
                # JobTrigger (entry point 2) is only responsible for:
                #   1) Updating iteration_count
                #   2) Checking max_iterations
                #   3) As fallback: only update status and next_run_time when entry point 1 hasn't updated status

                # Get current iteration_count
                current_iteration = job.iteration_count or 0
                new_iteration = current_iteration + 1

                # Check if max_iterations reached
                max_iterations = None
                if job.trigger_config:
                    max_iterations = job.trigger_config.max_iterations

                # Update last_run atomically (alpha + beta) for all ONGOING branches
                tz_name = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
                last_run_local = now.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
                await repo.update_last_run(job.job_id, now, last_run_local, tz_name)

                if max_iterations and new_iteration >= max_iterations:
                    # Reached max iterations, mark as COMPLETED
                    await repo.update_job(job.job_id, {
                        "status": JobStatus.COMPLETED.value,
                        "iteration_count": new_iteration,
                    })
                    await repo.clear_next_run(job.job_id)
                    if job.instance_id:
                        await self._update_instance_completed(job.instance_id)
                    logger.info(
                        f"Job {job.job_id} completed (ongoing, max_iterations={max_iterations} reached)"
                    )
                else:
                    # Continue execution
                    # First check if entry point 1 (hook_after_event_execution) has already updated the status
                    current_job = await repo.get_job(job.job_id)
                    current_status = current_job.status if current_job else JobStatus.RUNNING

                    # iteration_count is entry point 2 exclusive
                    await repo.update_job(job.job_id, {"iteration_count": new_iteration})

                    # Only recompute next_run and set status when hook didn't (status still RUNNING)
                    next_run_str = "N/A (set by hook)"
                    if current_status == JobStatus.RUNNING:
                        logger.warning(
                            f"Job {job.job_id}: status still RUNNING after hook, "
                            f"hook may have failed. Falling back to mechanical update."
                        )
                        next_run = compute_next_run(
                            job_type=job.job_type,
                            trigger_config=job.trigger_config,
                            last_run_utc=now,
                        )
                        if next_run:
                            await repo.update_next_run(job.job_id, next_run)
                            next_run_str = f"{next_run.local} ({next_run.tz})"
                        else:
                            await repo.clear_next_run(job.job_id)
                            next_run_str = "N/A"
                        await repo.update_job_status(job.job_id, JobStatus.ACTIVE)
                    else:
                        logger.info(
                            f"Job {job.job_id}: status={current_status.value} (updated by hook), "
                            f"respecting hook's decision."
                        )

                    logger.info(
                        f"Job {job.job_id} ongoing, iteration={new_iteration}"
                        f"{f'/{max_iterations}' if max_iterations else ''}, next run: {next_run_str}"
                    )

        except Exception as e:
            logger.exception(f"Error finalizing job {job.job_id}: {e}")

    async def _handle_job_failure(self, job: JobModel, error: str) -> None:
        """
        Handle job execution failure.

        Updates job status to FAILED and records the error.
        Optionally sends an error notification to the user's inbox.

        Args:
            job: JobModel instance
            error: Error message
        """
        try:
            # Update job status to FAILED with error message
            await self._get_job_repo().update_job_status(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                error_message=error
            )

            # Update Instance status to failed (triggers ModulePoller)
            if job.instance_id:
                await self._update_instance_failed(job.instance_id)

            logger.warning(f"Job {job.job_id} failed: {error}")

        except Exception as e:
            logger.exception(f"Error handling job failure for {job.job_id}: {e}")


# =============================================================================
# ModuleRunner Integration Entry Point
# =============================================================================

def run_job_trigger(
    poll_interval: int = 60,
    max_workers: int = 5
) -> None:
    """
    Run JobTrigger (called by ModuleRunner)

    This is the process entry function for JobTrigger.
    ModuleRunner calls this function in an independent process.

    Args:
        poll_interval: Polling interval (seconds)
        max_workers: Maximum concurrent worker count
    """
    import xyz_agent_context.settings  # noqa: F401 - Ensure .env is loaded

    # Don't create database client here, let start() lazy-load in async context
    trigger = JobTrigger(
        poll_interval=poll_interval,
        max_workers=max_workers
    )
    asyncio.run(trigger.start())


# =============================================================================
# Standalone Entry Point
# =============================================================================

def main():
    """CLI entry point for JobTrigger."""
    parser = argparse.ArgumentParser(
        description="JobTrigger - Background Task Executor (Worker Pool mode)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start with default settings (60s interval, 5 workers)
  uv run python -m xyz_agent_context.module.job_module.job_trigger

  # Start with 30s interval and 3 workers
  uv run python -m xyz_agent_context.module.job_module.job_trigger --interval 30 --workers 3

  # Run once (for testing)
  uv run python -m xyz_agent_context.module.job_module.job_trigger --once
"""
    )

    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=60,
        help="Poll interval in seconds (default: 60)"
    )

    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=5,
        help="Max concurrent workers (default: 5)"
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (for testing)"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    from xyz_agent_context.utils.logging import setup_logging
    setup_logging(
        "job_trigger",
        level="DEBUG" if args.debug else None,
    )

    logger.info("JobTrigger - Background Task Executor (Worker Pool)")
    logger.info(f"   Poll interval: {args.interval}s")
    logger.info(f"   Max workers: {args.workers}")
    logger.info(f"   Mode: {'Single run' if args.once else 'Continuous'}")
    logger.info("Press Ctrl+C to stop")

    if args.once:
        # Run once for testing (single poll, no worker pool)
        async def run_once():
            import xyz_agent_context.settings  # noqa: F401
            trigger = JobTrigger(
                poll_interval=args.interval,
                max_workers=args.workers
            )
            # Manually initialize database client
            trigger._db = await get_db_client()
            await trigger._poll_and_enqueue()
            logger.info(f"Single poll completed, {trigger._job_queue.qsize()} jobs in queue")

        asyncio.run(run_once())
    else:
        # Run continuously with Worker Pool
        run_job_trigger(args.interval, args.workers)


async def test_execute_single_job():
    """
    Test executing a single Job (for development debugging)

    Usage:
        uv run python -c "import asyncio; from xyz_agent_context.module.job_module.job_trigger import test_execute_single_job; asyncio.run(test_execute_single_job())"
    """
    import xyz_agent_context.settings  # noqa: F401 - Ensure .env is loaded

    database_client = await get_db_client()
    trigger = JobTrigger(database_client=database_client)

    # Build test Job
    job = JobModel(
        job_id="job_test_" + uuid4().hex[:8],
        title="AI News Summary Test",
        agent_id="agent_ecb12faf",
        user_id="user_demo",
        job_type=JobType.ONE_OFF,
        trigger_config=TriggerConfig(run_at=utc_now()),
        description="Test: collect AI domain news and generate summary.",
        payload="Search for today's important AI news, generate a summary report containing 3-5 news items.",
        created_at=utc_now(),
        updated_at=utc_now(),
        status=JobStatus.PENDING,
        next_run_time=utc_now(),
        process=[],
    )

    logger.info(f"Testing job execution: {job.job_id}")
    await trigger._execute_job(job)
    logger.info("Test completed")


if __name__ == "__main__":
    main()
    