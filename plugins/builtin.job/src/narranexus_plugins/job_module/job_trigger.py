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
    uv run python -m narranexus_plugins.job_module.job_trigger

    # With custom interval
    uv run python -m narranexus_plugins.job_module.job_trigger --interval 30
"""

import asyncio
import argparse
import os
from typing import List, Optional, Dict, Any, Set
from uuid import uuid4

from loguru import logger

# Schema
from narranexus.platform.schema.job_schema import (
    JobModel,
    JobOrigin,
    JobStatus,
    JobType,
    TriggerConfig,
)
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.schema.runtime_message import AUTH_EXPIRED_ERROR_TYPE
from narranexus.platform.schema.entity_schema import NON_TRANSACTING_USER_STATUSES
from narranexus.platform.agent_runtime.client import get_agent_runtime_client

# Utils
from narranexus.platform.utils import DatabaseClient, get_db_client, utc_now, format_for_llm
from narranexus.platform.utils.timezone import coerce_utc

# Repository
from narranexus.platform.repository import JobRepository
from narranexus.platform.repository.inbox_repository import InboxRepository
from narranexus.platform.repository.owner_notice_cooldown_repository import (
    OwnerNoticeCooldownRepository,
)
from narranexus.platform.schema.inbox_schema import InboxMessageType, MessageSource
from narranexus.platform.services.service_audit import ServiceAuditor
# Leaf shared util (the real-time circuit-breaker imports the same) — NOT a
# cross-module dependency (binding rule #3). Reused so the Job layer recognises
# the SAME deterministic, won't-heal-by-waiting failures (#110) that the
# real-time layer already treats as self-serviceable.
from narranexus.platform.agent_framework.llm.failure import (
    classify_self_serviceable,
    OUT_OF_CREDIT_REASONS,
    SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND,
)
from narranexus.platform.utils.job_scheduling import (
    compute_next_run,
    past_schedule_horizon,
)
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone

# Context builder (extracted: dependency outputs, social network, narrative, prompt assembly)
from narranexus_plugins.job_module._job_context_builder import build_execution_prompt


# error_type values (set by step_3_agent_loop's `error_type = type(e).__name__`)
# that mean "this run cannot succeed until the owner tops up quota or configures
# their own provider". On these we PAUSE the job (PAUSED_NO_QUOTA) instead of
# rescheduling — otherwise a recurring/ongoing job re-fires every interval into
# the same wall (the infinite-loop bug, #6). Transient errors (network / LLM
# hiccups) are deliberately NOT here: those still reschedule so the next
# interval can succeed. (铁律 #14: this is job-scheduler-level, never an
# agent_loop limit. #15: own-provider users' runs succeed and never hit this.)
_NO_QUOTA_ERROR_TYPES: frozenset = frozenset({
    "NoProviderConfiguredError",  # resolver: no usable provider for this user
    "LLMConfigNotConfigured",     # provider_driver resolution: no usable config
})

# Fallback substrings for the generic-except path that only carries a message.
# An empty free-tier WALLET is not here: the gateway refuses that call at
# runtime with an out-of-credit error, which layer 2 (classify_self_serviceable
# → OUT_OF_CREDIT_REASONS) already catches — one classifier, not two. Named as
# the SET, not a member: the 2026-07-30 split of free_tier_exhausted out of
# insufficient_balance is exactly what would have rotted a single name here.
_NO_QUOTA_ERROR_MARKERS = (
    "no provider configured",
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
# Spread OUT_OF_CREDIT_REASONS rather than naming one: a balance top-up leaves
# config unchanged for EVERY out-of-credit reason, so a new one must land here
# automatically — naming just `insufficient_balance` is how the free-tier split
# (2026-07-30) would otherwise have handed paused free-tier jobs back to the
# blind time-based backstop.
_EDGE_ONLY_RESUME_REASONS: frozenset[str] = frozenset({
    *OUT_OF_CREDIT_REASONS,
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

# Statuses an in-flight run (agent job_update / user Jobs-panel action) may
# have moved a job to while it executed. The SCHEDULED finalize branch
# respects these instead of overwriting them with ACTIVE + a fresh next_run
# (the guide agent's "stop reaching out" self-pause rides this). Module-level
# so future branches (ONGOING, recovery paths) reuse ONE definition.
_IN_RUN_STOPS = (
    JobStatus.PAUSED,
    JobStatus.PAUSED_NO_QUOTA,
    JobStatus.CANCELLED,
    JobStatus.COMPLETED,
    JobStatus.FAILED,
)

# PAUSED_NO_QUOTA recovery is edge-triggered (login / quota grant / preference
# toggle / provider save call rearm_user_no_quota_jobs). The poll scan is only a
# backstop for missed edges, so it runs at this low interval instead of every
# 60s cycle (high-frequency scanning was the oscillation amplifier).
_NO_QUOTA_BACKSTOP_INTERVAL_S = 900  # 15 minutes


# B-17 / review I8: one "job paused" inbox notice per (agent, job, reason)
# per this window. A PAUSED_NO_QUOTA job whose reason readiness CAN observe
# (auth / no_quota) is re-armed by the 15-minute backstop, fires, fails
# identically and pauses again — without a window that is one inbox row per
# schedule tick, forever. The window is keyed on the REASON too, so a change
# of reason (no_quota -> auth) is a new fact and notifies immediately. Lives
# in `owner_notice_cooldowns` (OwnerNoticeCooldownRepository) so a process
# restart does not re-notify. MUST stay far below the bus's
# NOTICE_COOLDOWN_RETENTION_DAYS (2 days) — its daily sweep deletes rows older
# than that, and a window longer than the sweep would re-open mid-flight
# (tests/job_module/test_job_pause_inbox_notification.py pins the ratio).
_PAUSE_NOTICE_COOLDOWN_SECONDS = 6 * 3600
_PAUSE_NOTICE_CATEGORY_PREFIX = "job_paused:"


def _pause_notice_category(pause_reason: str) -> str:
    """`owner_notice_cooldowns.category` for a pause notice: prefix + reason.
    The column is VARCHAR(32); every reason `_finalize_job_execution` /
    `_execute_job` can produce fits (longest: `insufficient_balance`, 31)."""
    return f"{_PAUSE_NOTICE_CATEGORY_PREFIX}{pause_reason}"


def _compute_cooldown_seconds(consecutive_failures: int) -> int:
    """Exponential backoff: base · 2^(n-1), clamped to the cap.
    n=1→60s, 2→120s, 3→240s, … capped at 3600s (1h)."""
    n = max(1, consecutive_failures)
    return min(_BACKOFF_BASE_SECONDS * (2 ** (n - 1)), _BACKOFF_CAP_SECONDS)


# B-14: env-tunable daily spend circuit, evaluated before each scheduled
# start. It counts the executing USER's ENTIRE LLM spend for the local day —
# every `cost_records` row attributed to that user (interactive chat, memory
# consolidation, helper calls, other jobs), not only job runs. `cost_records`
# carries no job attribution, and the ceiling means "this user is done
# spending for today", which is a property of the user, not of one job —
# hence USER in the name. 0 (or unset) = disabled: the default is a no-op so
# this never changes existing behavior for anyone who hasn't opted in. Read
# fresh on every check (not a module-level constant) so ops can tune it
# without a process restart.
_USER_DAILY_SPEND_CAP_ENV = "NARRANEXUS_USER_DAILY_SPEND_CAP_USD"


def local_day_start_utc(tz_name: str, now: Optional[datetime] = None) -> datetime:
    """Midnight of the CURRENT local day in `tz_name`, as an aware UTC instant.

    "Today" for the spend cap is the job's own frozen timezone (the same
    `trigger_config.timezone` every other time judgement in this file uses),
    never UTC: a UTC-based day would reset a UTC+8 user's budget at 08:00
    local and straddle two calendar days. An unknown zone name falls back to
    UTC (loudly) rather than failing the whole cap check.
    """
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError / KeyError / bad type
        logger.warning(f"[spend-cap] unknown timezone {tz_name!r}; using UTC for the day boundary")
        tz = timezone.utc
    now_local = (now or utc_now()).astimezone(tz)
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def _daily_spend_usd_for_user(
    db, user_id: str, tz_name: str = "UTC", now: Optional[datetime] = None
) -> float:
    """Sum `cost_records.total_cost_usd` for `user_id` since the start of the
    current local day in `tz_name` (B-14 / review I6).

    Raw SQL — no repository exists for `cost_records` yet; unquoted
    identifiers, dialect-portable (tests/job_module/test_job_daily_spend_cap.py
    + its `_mysql` twin).

    The SQL only PRE-FILTERS (one full day of margin before the boundary);
    the exact `>= local midnight` cut is applied in Python on the parsed
    timestamps. SQLite stores `created_at` as text in two shapes — the column
    default `datetime('now')` writes `YYYY-MM-DD HH:MM:SS`, explicit writes
    land as ISO `YYYY-MM-DDTHH:MM:SS+00:00` — and those two do not order
    correctly against each other within one date (`T` > space), so no single
    string cutoff can be exact on the boundary date. A day of margin makes
    the string comparison only ever include EXTRA rows, never drop a valid
    one; the Python step (`coerce_utc`, which reads both shapes and MySQL's
    native DATETIME) discards the surplus. The cutoff travels as a
    `YYYY-MM-DD HH:MM:SS` string rather than a datetime object: passing a
    datetime relied on CPython's default sqlite3 adapter, deprecated since
    3.12 (review M6).

    ASSUMES the DB session clock is UTC: `cost_records.created_at` is filled by
    the column default (`datetime('now')` on SQLite — always UTC;
    `CURRENT_TIMESTAMP(6)` on MySQL — the SESSION time zone). A MySQL session
    not in UTC shifts this whole window by the offset. The MySQL twin asserts
    `NOW() = UTC_TIMESTAMP()` so a mis-configured server fails there, not in
    production arithmetic.

    `now` exists for tests to pin the day boundary; production callers leave
    it None.
    """
    day_start = local_day_start_utc(tz_name, now)
    prefilter = (day_start - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    query = """
        SELECT total_cost_usd, created_at
        FROM cost_records
        WHERE user_id = %s AND created_at >= %s
    """
    rows = await db.execute(query, params=(user_id, prefilter), fetch=True)
    total = 0.0
    for row in rows or ():
        stamp = coerce_utc(row.get("created_at"))
        if stamp is None or stamp < day_start:
            continue
        total += float(row.get("total_cost_usd") or 0.0)
    return total


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
        from narranexus.platform.utils.db.schema_registry import auto_migrate
        await auto_migrate(self._db._backend)
        logger.info("Schema auto-migration complete")

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
                # Same cadence: a job parked by the daily spend cap comes back
                # on its own once the user's local day rolls over (spend back
                # under the cap) or ops lower/disable the cap — the cap is a
                # per-day ceiling, not a permanent stop (review C1).
                await self._resume_spend_capped_jobs()
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
            # One account lookup per principal per cycle, not per job (review M1).
            standing: Dict[str, Optional[str]] = {}
            for job in due_jobs:
                if job.job_id in self._running_jobs:
                    logger.debug(f"Job {job.job_id} already running, skipped")
                    continue

                # B-13: a non-transacting owner (banned / blocked / deleted)
                # must never be enqueued — `Key is blocked` 401s are
                # otherwise indistinguishable from an ordinary auth failure,
                # so the job cycled through PAUSED_NO_QUOTA and resurrected
                # daily even though the account itself (not the provider) is
                # what is unusable. Pause it here, BEFORE it ever reaches a
                # worker, so it also drops out of `get_due_jobs()` on the
                # next poll instead of being re-evaluated every cycle. The
                # execution principal (`related_entity_id or user_id`) is the
                # one identity this file judges everywhere (`_user_can_run`,
                # the spend cap, `_run_agent`).
                exec_uid = job.related_entity_id or job.user_id
                if exec_uid and exec_uid not in standing:
                    standing[exec_uid] = await self._non_transacting_status(exec_uid)
                blocked_status = standing.get(exec_uid) if exec_uid else None
                if blocked_status:
                    await repo.update_job(job.job_id, {
                        "status": JobStatus.PAUSED.value,
                        "paused_reason": blocked_status,
                        "paused_at": utc_now(),
                    })
                    logger.warning(
                        f"Job {job.job_id} paused: principal {exec_uid} is {blocked_status}"
                    )
                    continue

                self._running_jobs.add(job.job_id)
                await self._job_queue.put(job)
                self._enqueued_total += 1
                enqueued += 1

            if enqueued > 0:
                logger.info(f"Enqueued {enqueued} jobs (queue size: {self._job_queue.qsize()})")

        except Exception as e:
            logger.exception(f"Error in poll_and_enqueue: {e}")

    async def _non_transacting_status(self, user_id: str) -> Optional[str]:
        """The account's `users.status` if it is one that must not transact
        (`NON_TRANSACTING_USER_STATUSES`: banned / blocked / deleted), else None.

        B-13: a banned user's `Key is blocked` 401s look identical to a dead
        credential to every other classifier in this file, so a banned owner's
        scheduled job kept being treated as a normal auth failure and cycling
        through PAUSED_NO_QUOTA → resume-probe → fail again forever, resurrecting
        daily even though the account itself is what is actually unusable —
        no provider check can ever fix that. This is intentionally its OWN
        check ahead of any provider classification (never folded into
        `classify_provider_for_user`): account standing and provider readiness
        are orthogonal facts, and a non-transacting account must short-circuit
        before a provider probe is even attempted.

        Review I1: judged against the shared `NON_TRANSACTING_USER_STATUSES`
        (the same set the auth middleware / WS gate / admin suspend use), not
        `banned` alone — `blocked` / `deleted` accounts "equally must not
        transact" (entity_schema) and their jobs produced the same 401 storm.
        The status value is returned (not a bool) so the caller can record the
        real reason in `paused_reason`; none of these values matches
        `_EDGE_ONLY_RESUME_REASONS` or a resumable status, so they never
        trip an automatic resume.

        Fails OPEN (returns None) on a lookup error or a missing `users` row
        — a transient DB hiccup here must not freeze every job in the system,
        and these are explicit states, never the absence of a row.
        """
        try:
            row = await self.db.get_one("users", {"user_id": user_id})
            if not row:
                return None
            status = row.get("status")
            return status if status in NON_TRANSACTING_USER_STATUSES else None
        except Exception as e:  # noqa: BLE001 — best-effort, must never block polling
            logger.debug(f"_non_transacting_status lookup failed for {user_id}: {e}")
            return None

    async def _user_can_run(self, user_id: str) -> bool:
        """Would a run for this user resolve a provider right now?

        Delegates to the single classifier (`ProviderResolver.classify` via
        `classify_provider_for_user`) so this resume gate can never drift from
        the runtime again — that drift was the root cause of the 2026-05-31
        pause/resume oscillation. The OLD implementation checked
        "quota.check() OR own-provider-complete", which IGNORED
        the runtime's actual decision. (Historically that meant ignoring
        `prefer_system_override`; the user-facing preference is gone since
        2026-07-18 — free-tier-first is platform behavior — but the lesson
        stands: this gate must delegate to the classifier, never re-derive.)

        铁律 #15 is still honoured: the platform never overrides the user's
        provider choice — it only stops resuming a job into a run the
        runtime will refuse.

        B-13: checked BEFORE the provider classifier — a non-transacting
        account (banned / blocked / deleted) can never run regardless of
        provider readiness, and skipping the classifier call entirely avoids
        probing a provider for an account that is not allowed to transact.
        """
        if await self._non_transacting_status(user_id):
            return False
        try:
            from narranexus.platform.agent_framework.providers.resolver import (
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
            # One readiness check per principal per call, not per job (review M1).
            can_run: Dict[str, bool] = {}
            for job in paused:
                # Do NOT blind-probe reasons whose fix readiness can't observe
                # (balance top-up / model / context). Re-arming them every cycle
                # is exactly the retry storm — a balance-0 user reads as
                # config-complete → "can run" → re-arm → re-fail → forever. These
                # resume only on a real edge (rearm_user_no_quota_jobs) or manual.
                if job.paused_reason in _EDGE_ONLY_RESUME_REASONS:
                    continue
                exec_uid = job.related_entity_id or job.user_id
                if not exec_uid:
                    continue
                if exec_uid not in can_run:
                    can_run[exec_uid] = await self._user_can_run(exec_uid)
                if not can_run[exec_uid]:
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

    async def _resume_spend_capped_jobs(self) -> int:
        """Flip PAUSED_SPEND_CAP jobs back to ACTIVE once the executing user's
        spend for the CURRENT local day is under the cap again (review C1).

        The cap is a per-day ceiling: the same predicate that paused the job
        (`_daily_spend_cap_exceeded`, judged in the job's own timezone) is
        re-evaluated, so a job resumes exactly when a fresh start would have
        been allowed — the next local day, or as soon as ops lower/disable
        the cap. Deliberately NOT folded into `_resume_eligible_no_quota_jobs`
        / `rearm_user_no_quota_jobs`: those recover on provider readiness
        (login / provider save edges), which says nothing about spend and
        would revive a still-over-cap job into an immediate re-pause loop.

        Resumes schedule FORWARD (`compute_next_run` from now): a heartbeat
        job paused for a day must not replay the fires it missed. A recurring
        job whose next fire would land past its end_at horizon completes
        instead — the same rule as `_rearm_cooled_jobs` and
        `_heal_unscheduled_active_jobs` (the no-quota resume,
        `_resume_eligible_no_quota_jobs`, has no horizon check).
        """
        try:
            repo = self._get_job_repo()
            paused = await repo.get_jobs_by_status(JobStatus.PAUSED_SPEND_CAP)
            if not paused:
                return 0
            resumed = 0
            # One spend scan per (principal, timezone) per call, not per job (review M1).
            over_cap: Dict[tuple, bool] = {}
            for job in paused:
                exec_uid = job.related_entity_id or job.user_id
                user_tz = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
                if exec_uid:
                    key = (exec_uid, user_tz)
                    if key not in over_cap:
                        over_cap[key] = await self._daily_spend_cap_exceeded(exec_uid, user_tz)
                    if over_cap[key]:
                        continue
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
                        await self._update_instance_completed(job.instance_id)
                    logger.info(
                        f"Job {job.job_id} completed instead of resumed "
                        f"(PAUSED_SPEND_CAP resume past end_at horizon)"
                    )
                    continue
                if next_run:
                    await repo.update_next_run(job.job_id, next_run)
                await repo.update_job(job.job_id, {
                    "status": JobStatus.ACTIVE.value,
                    "paused_reason": None,
                    "paused_at": None,
                })
                resumed += 1
                logger.info(
                    f"Job {job.job_id} resumed from PAUSED_SPEND_CAP (user={exec_uid})"
                )
            if resumed:
                logger.info(f"Resumed {resumed} job(s) from PAUSED_SPEND_CAP")
            return resumed
        except Exception as e:
            logger.exception(f"Error resuming PAUSED_SPEND_CAP jobs: {e}")
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
                # A retry that would fire past the job's end_at horizon is a
                # fire the schedule no longer owes — complete instead of
                # re-arming (otherwise "runs until date X" leaks one extra
                # run through the failure-backoff door). Recurring types only:
                # a ONE_OFF has no "next fire" for a horizon to bound — its
                # single run hasn't succeeded yet, and completing it here
                # would mark a never-delivered reminder as done. The actual
                # retry instant is max(cu, now): cu is already <= now here,
                # and after long downtime the re-armed job fires immediately
                # (now), not at the stale cooldown time — using cu alone
                # would leak the very extra fire this branch exists to stop.
                retry_at = max(cu, now) if cu else now
                if job.job_type != JobType.ONE_OFF and past_schedule_horizon(
                    job.trigger_config, retry_at
                ):
                    await repo.update_job(job.job_id, {
                        "status": JobStatus.COMPLETED.value,
                        "cooldown_until": None,
                    })
                    await repo.clear_next_run(job.job_id)
                    if job.instance_id:
                        await self._update_instance_completed(job.instance_id)
                    logger.info(
                        f"Job {job.job_id} completed instead of re-armed "
                        f"(COOLING retry past end_at horizon)"
                    )
                    continue
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
                    # Don't heal a zombie back to life past its end_at horizon
                    # — the schedule it would resume no longer owes any fires.
                    if past_schedule_horizon(job.trigger_config, next_run.utc):
                        await repo.update_job_status(job.job_id, JobStatus.COMPLETED)
                        if job.instance_id:
                            await self._update_instance_completed(job.instance_id)
                        logger.warning(
                            f"Job {job.job_id} completed instead of healed "
                            f"(ACTIVE zombie past end_at horizon)"
                        )
                        continue
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

            # 1.4 Daily spend cap (B-14): checked BEFORE building the prompt
            # or calling the framework — this only gates the NEXT scheduled
            # start, exactly like the existing PAUSED_NO_QUOTA gate, and
            # never interrupts a run already in flight (铁律 #14). "Today" is
            # the job's frozen timezone — the same one the prompt below uses.
            exec_uid = job.related_entity_id or job.user_id
            user_tz = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
            if exec_uid and await self._daily_spend_cap_exceeded(exec_uid, user_tz):
                await self._get_job_repo().update_job(job.job_id, {
                    "status": JobStatus.PAUSED_SPEND_CAP.value,
                    "paused_reason": "spend_cap",
                    "paused_at": utc_now(),
                })
                logger.warning(
                    f"Job {job.job_id} paused: daily spend cap exceeded for {exec_uid}"
                )
                await self._notify_owner_job_paused(
                    job, "spend_cap",
                    "Your total LLM spend for today has reached the daily cap. "
                    "This job resumes automatically once the next day starts "
                    "(in the job's timezone) and spend is back under the cap.",
                )
                return

            # 1.5 Update associated Instance status (for ModulePoller detection)
            if job.instance_id:
                await self._update_instance_for_execution(job.instance_id)

            # 2. Build execution Prompt (including dependency Job outputs)
            # Use job's own timezone (frozen at creation); do NOT read users.timezone
            # which may have changed since the job was scheduled.
            prompt = await build_execution_prompt(self.db, job, user_tz)
            logger.debug(f"Built prompt for job {job.job_id}: {prompt[:100]}...")

            # 3. Call AgentRuntime
            # Agent will send report to user via notify_owner
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
        to the user via notify_owner.

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
                failure_note = (
                    f"⚠️ Scheduled task failed: {collection.error.error_message}"
                )
                # A room-origin job that fails must not leave the room silent.
                #
                # The owner-chat path has somewhere to surface this (the job
                # row, the Jobs panel, `job.last_error`); a room has nothing —
                # four people watched someone ask for a reminder and would
                # simply never hear about it again. That is the same broken
                # hand-off this whole change is about, one surface over, and
                # the team room already answers it for a fatal turn (see
                # `message_bus_trigger`'s `turn.fatal` notice: a teammate
                # cannot tell "not interested" from "broken").
                await self._deliver_to_origin(
                    job, failure_note, run_event_id=collection.event_id
                )
                return {
                    "event_id": event_id,
                    "content": failure_note,
                    "success": False,
                    "error": collection.error.error_message,
                    "error_type": collection.error.error_type,
                    "tool_calls": collection.tool_calls,
                }

            content = collection.output_text
            tool_calls = collection.tool_calls

            # Delivered BEFORE the empty-output boilerplate below is synthesised.
            #
            # That boilerplate ("## Task Completed … Job ID … Tools used: None")
            # is written for the owner's inbox, where an operational record is
            # the right thing to leave. A room is not an inbox: the prompt this
            # job ran under told it "your reply IS the report, it is posted to
            # the room", so posting a metadata block on its behalf puts a
            # platform-shaped notice in front of four people and reads as a bug
            # rather than as "the job had nothing to say".
            #
            # The owner path keeps the boilerplate untouched — PRD acceptance
            # #8 requires the private-chat behaviour to be unchanged.
            room_content = content

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

            await self._deliver_to_origin(
                job, room_content, run_event_id=collection.event_id
            )

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

    async def _deliver_to_origin(
        self, job: JobModel, content: str, *, run_event_id: Optional[str] = None
    ) -> None:
        """FALLBACK: post a room-origin job's report if the run did not.

        The other half of the origin pair: `_job_context_builder` picked the
        prompt from `job.origin_source`, and this picks the delivery from the
        same field, so the two can never describe different surfaces. An
        owner-chat job (empty origin) returns immediately and keeps the
        historical path — the agent's own owner-facing call during the run
        (PRD acceptance #8).

        Why this became a fallback (2026-08-17). It used to be the ONLY path:
        the room's contract was that a job's plain text auto-posts, and the
        prompt said so. That contract is gone — the team room was the single
        surface in the platform where "your plain text reaches nobody" was
        false, and every layer that states the general rule was contradicting
        the one layer that carved it out. A team post is a tool call now, so
        the room prompt tells the job to call `message_team` and the primary
        path is the same one every other surface uses.

        What did NOT change is the guarantee that motivated the original: the
        room that asked always hears back. `has_message_from_turn` answers
        "did this run put anything in that room" exactly (event id, not a
        timestamp window), so the platform copy goes out only when the answer
        is no. A job that posted its own report is not double-posted; a job
        that produced a report and delivered it nowhere still reaches the four
        people waiting on it.

        Never raises. The job itself SUCCEEDED — its status, its narrative and
        its next_run_time are all correct — so a failed post must not turn a
        completed job into a failed one. It is logged loudly instead: an
        undeliverable report is a real problem, just not this job's failure.
        """
        source = (job.origin_source or "").strip()
        channel_id = (job.origin_channel_id or "").strip()
        if source not in JobOrigin.DELIVERABLE or not channel_id or not content.strip():
            return
        try:
            # Imported here: the job process builds its bus service lazily, and
            # a module-level import would tie job startup to the bus package.
            from narranexus.platform.message_bus.local_bus import LocalMessageBus
            from narranexus.platform.message_bus.multipart import (
                MAX_MULTIPART_TOTAL_BYTES,
                split_for_bus,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            db = await get_db_client()
            bus = LocalMessageBus(backend=db._backend)
            # The run's own post, if it made one, carries this same event id —
            # so this is an identity check, not a "was something posted around
            # then" guess. No event id means we cannot tell, and the safe
            # answer there is to deliver: a duplicate report is noise, a
            # missing one is the bug this fallback exists for.
            if run_event_id and await bus.has_message_from_turn(
                channel_id, job.agent_id, run_event_id
            ):
                logger.info(
                    f"[job-origin] {job.job_id} posted its own report to "
                    f"{channel_id}; platform copy skipped"
                )
                return
            # The bus refuses a row over MAX_BUS_MESSAGE_BYTES (never trims),
            # so a long report travels as ordered parts — posted back to back
            # from this one coroutine, which is what keeps the write edge's
            # "same sender's latest part" chain contiguous (the agent's own
            # message_team posts are ordinary rows and do not break it). Past
            # the whole-message budget the room is TOLD, never left silent.
            #
            # Two costs of parts landing in a TEAM room, accepted (r3 M4): the
            # transcript renders a report as N raw part rows (the frontend
            # does not read the part columns), and if an origin channel's
            # owner is an agent, that agent's lane is held up to the grace
            # should this coroutine die between parts. No member turn is
            # started by the parts themselves (no mentions).
            # Known gap (r3 M5): two >60 KB reports from the SAME agent into
            # the SAME room at the same instant would interleave their part
            # chains and the write edge would refuse the second's part 2 —
            # caught below as a logged error. Rare enough not to lock here.
            body_bytes = len(content.encode("utf-8"))
            if body_bytes > MAX_MULTIPART_TOTAL_BYTES:
                # Posted as the AGENT (not a PLATFORM_MSG_TYPES line) on
                # purpose: it must carry this run's event_id / root_run_id so
                # "view reasoning & tools" and cascade stop still reach the run
                # that produced the too-long report (r3 M7).
                pieces = [
                    f"[platform] This job's report is {body_bytes} bytes, over the "
                    f"{MAX_MULTIPART_TOTAL_BYTES}-byte limit for one message, so it "
                    f"was not posted here. It is kept in the job's run record."
                ]
            else:
                pieces = split_for_bus(content)
            for index, piece in enumerate(pieces, start=1):
                await bus.send_message(
                    from_agent=job.agent_id,
                    to_channel=channel_id,
                    content=piece,
                    # Recorded so the room can tell a scheduled report from a
                    # live reply, and so the recipient's turn-source logic does
                    # not read it as a peer asking a question.
                    sender_turn_source=WorkingSource.JOB,
                    # A job report is the THIRD way an agent's words enter a
                    # room, after a live reply and a patrol line. Both of the
                    # others stamp these, and the room's transcript reads
                    # `event_id` to offer "view reasoning & tools" — without it
                    # this line has no visible provenance, which is worse here
                    # than elsewhere because nobody in the room saw the turn.
                    event_id=run_event_id or None,
                    # Lineage. A job execution has no parent run — a timer woke
                    # it, not another agent — so it is the ROOT of its own tree,
                    # and `schema_registry` defines a root as storing its own
                    # event id. Whoever this report wakes next inherits that
                    # label and stays reachable by a cascade stop.
                    root_run_id=run_event_id or None,
                    # Deliberately no `mentions`: a report is a notice, not a
                    # request. An @ would wake a team turn immediately AND open
                    # a fresh errand for a hand-off nobody made.
                    part_index=index if len(pieces) > 1 else 0,
                    part_count=len(pieces) if len(pieces) > 1 else 0,
                )
            logger.info(
                f"[JobTrigger] job {job.job_id} reported into {channel_id} "
                f"(origin={source})"
            )
        except Exception as e:  # noqa: BLE001 — see docstring
            logger.error(
                f"[JobTrigger] job {job.job_id} produced a report but could not "
                f"post it to {channel_id}: {type(e).__name__}: {e}"
            )

    async def _daily_spend_cap_exceeded(self, user_id: str, tz_name: str = "UTC") -> bool:
        """True when `user_id`'s total LLM spend for the current local day
        (in `tz_name`) already meets/exceeds NARRANEXUS_USER_DAILY_SPEND_CAP_USD
        (B-14). 0/unset = disabled. See `_daily_spend_usd_for_user` for what
        is counted and which day boundary applies.

        Fails OPEN (returns False) on a parse error or a DB lookup failure —
        a transient DB hiccup here must not block every scheduled job in the
        system, and the platform never becomes the interruption source for
        its own bugs (铁律 #14/#15's spirit, applied to this new gate).
        """
        cap_str = os.getenv(_USER_DAILY_SPEND_CAP_ENV, "0")
        try:
            cap = float(cap_str)
        except ValueError:
            logger.warning(
                f"[job-spend-cap] {_USER_DAILY_SPEND_CAP_ENV}={cap_str!r} is not "
                f"a number; treating as disabled"
            )
            return False
        if cap <= 0:
            return False
        try:
            spent = await _daily_spend_usd_for_user(self.db, user_id, tz_name)
        except Exception as e:  # noqa: BLE001 — best-effort, must never block polling
            logger.warning(f"[job-spend-cap] lookup failed for {user_id}: {e}")
            return False
        return spent >= cap

    async def _notify_owner_job_paused(
        self, job: JobModel, pause_reason: str, detail: str
    ) -> None:
        """Notify the job owner via inbox that a scheduled job has been
        paused (B-17). Reuses the existing inbox notification mechanism
        (same InboxRepository / InboxMessageType.SYSTEM_NOTICE pattern the
        real-time agent circuit breaker already uses in
        services/background_llm_alerts.alert_agent_paused) rather than
        inventing a new channel. Best-effort: a notification failure must
        never break the pause itself.

        De-duplicated per (agent, job, reason) over
        `_PAUSE_NOTICE_COOLDOWN_SECONDS` via `owner_notice_cooldowns`
        (review I8). The cooldown read fails OPEN — an unreadable window
        must not silence a real pause — and the window is armed only after
        the inbox write succeeded, so a failed write does not open a window
        with nothing behind it (same shape as background_llm_alerts).
        """
        recipient = job.user_id
        if not recipient:
            return
        category = _pause_notice_category(pause_reason)
        try:
            if await OwnerNoticeCooldownRepository(self.db).is_cooling(
                job.agent_id, job.job_id, category, _PAUSE_NOTICE_COOLDOWN_SECONDS
            ):
                logger.info(
                    f"[job-pause] owner notice for {job.job_id} ({pause_reason}) "
                    f"still inside its cooldown window; not re-sent"
                )
                return
        except Exception as e:  # noqa: BLE001 — a duplicate notice beats a silent window
            logger.warning(f"[job-pause] cooldown read failed for {job.job_id}: {e}")
        try:
            from narranexus.platform.agent_framework.llm.failure import redact_secrets

            safe_detail = redact_secrets(detail) if detail else ""
            content = (
                f"Scheduled job \"{job.title}\" has been paused and will not "
                f"run again automatically (reason: {pause_reason})."
                + (f"\n\nDetail: {safe_detail}" if safe_detail else "")
                + "\n\nResolve the underlying issue (top up / reconfigure the "
                "provider for this Agent, or reduce spend if this is a "
                "budget cap), then resume the job from the Jobs panel."
            )
            await InboxRepository(self.db).create_message(
                user_id=recipient,
                message_id=f"jobpause_{uuid4().hex[:16]}",
                title=f"Job paused: {job.title}",
                content=content,
                message_type=InboxMessageType.SYSTEM_NOTICE,
                source=MessageSource(type="job", id=job.job_id),
            )
        except Exception as e:  # noqa: BLE001 — notification is best-effort
            logger.warning(
                f"[job-pause] owner inbox notice failed for {job.job_id}: {e}"
            )
            return
        try:
            await OwnerNoticeCooldownRepository(self.db).arm(job.agent_id, job.job_id, category)
        except Exception as e:  # noqa: BLE001 — best-effort; a duplicate later is the cheap failure
            logger.warning(f"[job-pause] cooldown arm failed for {job.job_id}: {e}")

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
                # B-17: the owner had no in-product signal that a scheduled
                # job silently stopped running — only a Jobs-panel status
                # they'd have to go look for.
                await self._notify_owner_job_paused(
                    job, pause_reason, result.get("error") or ""
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

            # Success: clear any prior transient error / backoff state before the
            # normal complete/reschedule branching below. `last_error` is cleared
            # on EVERY success — not only when backoff was set — because it can
            # also be left behind by `recover_all_running_jobs` startup recovery,
            # which writes `last_error` ("Process restarted, auto-recovered")
            # WITHOUT touching the failure counter. Otherwise a job that recovered
            # and has been succeeding every interval keeps surfacing a long-
            # resolved error in the UI (incident 2026-07-30).
            has_backoff = (job.consecutive_failure_count or 0) > 0 or job.cooldown_until is not None
            if job.last_error or has_backoff:
                reset: dict = {"last_error": None}
                if has_backoff:
                    reset["consecutive_failure_count"] = 0
                    reset["cooldown_until"] = None
                    reset["paused_reason"] = None
                await repo.update_job(job.job_id, reset)

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

                # Respect an in-run STOP (same re-read the ONGOING branch
                # has always done): the agent may have paused THIS job mid-run
                # via job_update (e.g. the onboarding guide's "stop reaching
                # out" self-pause), or the user paused/cancelled it from the
                # Jobs panel while it ran. `job` is a pre-execution snapshot;
                # blindly writing ACTIVE + next_run here used to resurrect the
                # schedule right after the agent promised to stop. Only the
                # explicit stop states are respected — anything else (RUNNING,
                # and the pending/active a test harness or an odd caller may
                # hold) reschedules exactly as before. last_run above is a
                # fact record and stays; everything from here down is
                # scheduling, which an in-run stop owns. This also keeps the
                # horizon path below from stamping COMPLETED over a mid-run
                # CANCELLED.
                current_job = await repo.get_job(job.job_id)
                current_status = current_job.status if current_job else job.status
                if current_status in _IN_RUN_STOPS:
                    # Terminal/paused ⇒ no next fire: every other terminal
                    # path in this file clears next_run, and try_acquire_job
                    # doesn't — without this, a job cancelled mid-run keeps a
                    # stale past next_run_time on its row (harmless to the
                    # poller, which filters on status, but dirty for the Jobs
                    # panel and any next_run-based queries). A user resume
                    # recomputes it (job_service re-derives next_run on
                    # status→active without an explicit one).
                    await repo.clear_next_run(job.job_id)
                    # The RUN still finished — instance completion (which
                    # feeds ModulePoller's post-run processing) is a per-run
                    # fact for scheduled jobs and is not the schedule's
                    # business, so it happens on every path.
                    if job.instance_id:
                        await self._update_instance_completed(job.instance_id)
                    logger.info(
                        f"Job {job.job_id}: status={current_status.value} changed "
                        f"in-run, respecting it (no reschedule)"
                    )
                    return

                next_run = compute_next_run(
                    job_type=job.job_type,
                    trigger_config=job.trigger_config,
                    last_run_utc=now,
                )
                # Scheduling horizon (trigger_config.end_at): a recurring job
                # whose NEXT fire would land past its horizon is done — the
                # platform completes it here, no model cooperation needed.
                # Only the horizon path completes; a None next_run keeps the
                # historical semantics (ACTIVE with NULL next_run) untouched.
                if next_run and past_schedule_horizon(job.trigger_config, next_run.utc):
                    await repo.clear_next_run(job.job_id)
                    await repo.update_job_status(
                        job_id=job.job_id,
                        status=JobStatus.COMPLETED
                    )
                    if job.instance_id:
                        await self._update_instance_completed(job.instance_id)
                    end_at = job.trigger_config.end_at if job.trigger_config else None
                    logger.info(
                        f"Job {job.job_id} completed (scheduled, end_at horizon "
                        f"{end_at} {tz_name} reached)"
                    )
                    return

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
                # Note: end_condition is primarily checked by after_turn (entry point 1)
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
                    # First check if entry point 1 (after_turn) has already updated the status
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
                        # end_at horizon, same semantics as the SCHEDULED
                        # branch — the primitive is generic to recurring jobs,
                        # and ONGOING is the type that most needs a platform
                        # brake (its end_condition is model-judged). Both arms
                        # honor it: this one when the hook failed (mechanical
                        # fallback), and the else-branch below when the hook
                        # rescheduled past the horizon.
                        if next_run and past_schedule_horizon(job.trigger_config, next_run.utc):
                            await repo.update_job(job.job_id, {
                                "status": JobStatus.COMPLETED.value,
                            })
                            await repo.clear_next_run(job.job_id)
                            if job.instance_id:
                                await self._update_instance_completed(job.instance_id)
                            logger.info(
                                f"Job {job.job_id} completed (ongoing, end_at "
                                f"horizon reached)"
                            )
                            return
                        if next_run:
                            await repo.update_next_run(job.job_id, next_run)
                            next_run_str = f"{next_run.local} ({next_run.tz})"
                        else:
                            await repo.clear_next_run(job.job_id)
                            next_run_str = "N/A"
                        await repo.update_job_status(job.job_id, JobStatus.ACTIVE)
                    else:
                        # Hook (entry point 1) owns the schedule decision now
                        # — EXCEPT the end_at horizon. The horizon is not a
                        # call the LLM makes (it decides end_condition; whether
                        # the schedule still owes a next fire is declared
                        # platform-side, _job_lifecycle.py), so if the hook
                        # rescheduled a fire that lands past end_at, complete
                        # instead of respecting it. This is the NORMAL ongoing
                        # path (the mechanical-fallback arm above only runs
                        # when the hook failed). A hook that already finished
                        # the job (COMPLETED/FAILED) cleared next_run →
                        # next_run_time is None → we skip and never
                        # double-complete (which would re-fire instance
                        # completion). next_run_time may round-trip naive from
                        # the DB, so normalize to aware-UTC before comparing
                        # (same as _rearm_cooled_jobs).
                        #
                        # _IN_RUN_STOPS guard (same as the SCHEDULED branch):
                        # an in-run stop — a user pause/cancel from the Jobs
                        # panel, or the hook writing a terminal/paused status —
                        # owns the outcome. Without this, a stale or hook-set
                        # crossed next_run below would stamp COMPLETED over a
                        # mid-run CANCELLED/PAUSED. No per-run instance
                        # completion on this respect path (unlike SCHEDULED) —
                        # that difference is intentional for ONGOING.
                        hook_next = current_job.next_run_time if current_job else None
                        if current_status not in _IN_RUN_STOPS and hook_next is not None:
                            if hook_next.tzinfo is None:
                                hook_next = hook_next.replace(tzinfo=timezone.utc)
                            if past_schedule_horizon(job.trigger_config, hook_next):
                                await repo.update_job(job.job_id, {
                                    "status": JobStatus.COMPLETED.value,
                                })
                                await repo.clear_next_run(job.job_id)
                                if job.instance_id:
                                    await self._update_instance_completed(job.instance_id)
                                logger.info(
                                    f"Job {job.job_id} completed (ongoing, end_at "
                                    f"horizon reached; hook had rescheduled past it)"
                                )
                                return
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
    import narranexus.platform.settings  # noqa: F401 - Ensure .env is loaded

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
  uv run python -m narranexus_plugins.job_module.job_trigger

  # Start with 30s interval and 3 workers
  uv run python -m narranexus_plugins.job_module.job_trigger --interval 30 --workers 3

  # Run once (for testing)
  uv run python -m narranexus_plugins.job_module.job_trigger --once
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

    from narranexus.platform.utils.logging import setup_logging
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
            import narranexus.platform.settings  # noqa: F401
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
        uv run python -c "import asyncio; from narranexus_plugins.job_module.job_trigger import test_execute_single_job; asyncio.run(test_execute_single_job())"
    """
    import narranexus.platform.settings  # noqa: F401 - Ensure .env is loaded

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
    