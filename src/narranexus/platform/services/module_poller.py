"""
ModulePoller - Generic module polling service

@file_name: module_poller.py
@author: NetMind.AI
@date: 2025-12-25
@description: Background service that detects Instance status changes and triggers callbacks

=============================================================================
Overview
=============================================================================

ModulePoller is a generic background polling service responsible for:
1. Periodically polling the module_instances table to detect status changes (in_progress -> completed)
2. Calling InstanceHandler.handle_completion() to handle dependency relationships
3. Triggering execution of newly activated instances (via AgentRuntime._execute_callback_instance)

This is a generic Module capability, not limited to JobModule; any Module that needs
asynchronous completion can leverage it.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │                      ModulePoller (Worker Pool)                      │
    │                                                                      │
    │   ┌─────────────┐                                                   │
    │   │   Poller    │ -> Poll DB, detect status changes, enqueue        │
    │   └─────────────┘                                                   │
    │          │                                                           │
    │          ▼                                                           │
    │   ┌─────────────┐                                                   │
    │   │   Queue     │ -> Pending completed instances                    │
    │   └─────────────┘                                                   │
    │          │                                                           │
    │          ▼                                                           │
    │   ┌─────────────────────────────────────┐                           │
    │   │  Worker 1  │  Worker 2  │  Worker N │                           │
    │   └─────────────────────────────────────┘                           │
    │          │                                                           │
    │          ▼                                                           │
    │   InstanceHandler.handle_completion()                               │
    │          │                                                           │
    │          ▼                                                           │
    │   AgentRuntime._execute_callback_instance()                         │
    └─────────────────────────────────────────────────────────────────────┘

Usage:
    # Run standalone
    uv run python -m narranexus.platform.services.module_poller

    # Custom parameters
    uv run python -m narranexus.platform.services.module_poller --interval 10 --workers 3
"""

import asyncio
import argparse
from datetime import datetime
from typing import List, Optional, Dict, Any, Set
from dataclasses import dataclass

from loguru import logger

# Schema
from narranexus.platform.schema.instance_schema import (
    ModuleInstanceRecord,
    InstanceStatus,
    LinkType,
)

# Utils
from narranexus.platform.utils import AsyncDatabaseClient, get_db_client

# Repository
from narranexus.platform.repository import (
    InstanceRepository,
    InstanceNarrativeLinkRepository,
)

# L2 observability — see services/service_audit.py
from narranexus.platform.services.service_audit import ServiceAuditor

# Dependency activation is edge-triggered (a completion the poller SEES, via
# `_find_completed_instances`, for narrative-bound AND narrative-less
# instances). The reconciliation below is the backstop for the narrative-less
# edges it cannot see — an upstream that finished before its dependent's
# BLOCKED row was written, or a completion lost to a restart / a failed
# callback — and runs at this low cadence, not every 5-second cycle (review
# I10). Each pass walks every narrative-less BLOCKED row in keyset pages; a
# page bounds each query and each handler call.
_BLOCKED_RECONCILE_INTERVAL_S = 900  # 15 minutes
_BLOCKED_RECONCILE_PAGE = 200  # rows per keyset page (query + handler call bound)


@dataclass
class CompletedInstanceInfo:
    """Completed Instance info (used for queue passing)"""
    instance_id: str
    # None for a narrative-less instance (no instance_narrative_links row at
    # all, e.g. a /api/jobs/complex Job) — resolved by
    # `InstanceHandler.handle_completion_no_narrative`.
    narrative_id: Optional[str]
    agent_id: str
    user_id: Optional[str]
    module_class: str


class ModulePoller:
    """
    Generic module polling service

    Core responsibilities:
    1. Periodically poll the database to detect Instance status changes (in_progress -> completed)
    2. Call InstanceHandler.handle_completion() to handle dependency relationships
    3. Trigger execution of newly activated instances

    Design features:
    - Worker Pool architecture: 1 Poller + N Workers
    - Status change detection: via last_polled_status field
    - Duplicate processing prevention: via callback_processed field
    """

    # =========================================================================
    # Initialization
    # =========================================================================

    def __init__(
        self,
        poll_interval: int = 5,
        max_workers: int = 3,
        database_client: Optional[AsyncDatabaseClient] = None
    ):
        """
        Initialize ModulePoller

        Args:
            poll_interval: Polling interval (seconds), default 5 seconds
            max_workers: Maximum concurrent worker count, default 3
            database_client: Database client (optional, lazy-loaded if not provided)
        """
        self.poll_interval = poll_interval
        self.max_workers = max_workers
        self._db = database_client
        self.running = False

        # Repository (lazy initialization)
        self._instance_repo: Optional[InstanceRepository] = None
        self._link_repo: Optional[InstanceNarrativeLinkRepository] = None

        # Worker Pool related
        self._task_queue: asyncio.Queue[CompletedInstanceInfo] = asyncio.Queue()
        self._processing_instances: Set[str] = set()  # instance_ids currently being processed
        self._workers: List[asyncio.Task] = []
        self._poller_task: Optional[asyncio.Task] = None

        # When the BLOCKED-instance reconciliation last ran (see _poll_and_enqueue).
        self._last_blocked_reconcile: Optional[datetime] = None

        # L2 observability — stale heartbeat reveals a wedged poll loop
        # that L1 "process alive" cannot catch (incident lesson #4).
        self.audit = ServiceAuditor("module_poller")

        logger.info(
            f"ModulePoller initialized: poll_interval={poll_interval}s, "
            f"max_workers={max_workers}"
        )

    @property
    def db(self) -> AsyncDatabaseClient:
        """Get database client (must be used after start())"""
        if self._db is None:
            raise RuntimeError("Database client not initialized. Call start() first.")
        return self._db

    def _get_instance_repo(self) -> InstanceRepository:
        """Get or create InstanceRepository instance"""
        if self._instance_repo is None:
            self._instance_repo = InstanceRepository(self.db)
        return self._instance_repo

    def _get_link_repo(self) -> InstanceNarrativeLinkRepository:
        """Get or create InstanceNarrativeLinkRepository instance"""
        if self._link_repo is None:
            self._link_repo = InstanceNarrativeLinkRepository(self.db)
        return self._link_repo

    # =========================================================================
    # Lifecycle management
    # =========================================================================

    async def start(self) -> None:
        """
        Start ModulePoller (Worker Pool mode)

        Architecture:
        - 1 Poller coroutine: periodically queries instances with status changes and enqueues them
        - N Worker coroutines: dequeue tasks and process callbacks
        """
        # Initialize database client in async context
        if self._db is None:
            self._db = await get_db_client()
            logger.info("Database client initialized in async context")

        # Ensure all tables exist (poller runs as separate process)
        from narranexus.platform.utils.db.schema_registry import auto_migrate
        await auto_migrate(self._db._backend)
        logger.info("Schema auto-migration complete")

        logger.info("ModulePoller starting (Worker Pool mode)...")
        logger.info(f"   Poll interval: {self.poll_interval} seconds")
        logger.info(f"   Max workers: {self.max_workers}")
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
            logger.info("ModulePoller tasks cancelled")

        logger.info("ModulePoller stopped")

    async def stop(self) -> None:
        """
        Gracefully stop ModulePoller

        1. Set running=False to stop the poller from enqueuing
        2. Wait for queued tasks to be processed
        3. Cancel all workers
        """
        logger.info("Stopping ModulePoller gracefully...")
        self.running = False
        await self.audit.stopped()

        # Wait for queue to drain (up to 30 seconds)
        try:
            await asyncio.wait_for(self._task_queue.join(), timeout=30)
            logger.info("All queued tasks completed")
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
        logger.info("ModulePoller shutdown complete")

    # =========================================================================
    # Worker Pool core
    # =========================================================================

    async def _poller(self) -> None:
        """
        Poller coroutine: periodically queries instances with status changes and enqueues them

        Detection conditions:
        - status = 'completed' (or 'failed')
        - last_polled_status = 'in_progress'
        - callback_processed = FALSE
        """
        while self.running:
            try:
                await self._poll_and_enqueue()
                # Throttled L2 heartbeat — a stale row means the loop wedged
                # though the process is still alive (incident lesson #4).
                await self.audit.heartbeat()
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
        Worker coroutine: dequeue tasks and process callbacks

        Args:
            worker_id: Worker number (used for logging)
        """
        logger.debug(f"Worker {worker_id} ready")

        while True:
            try:
                # Get task from queue (blocking wait)
                info = await self._task_queue.get()

                try:
                    logger.info(f"[Worker {worker_id}] Processing: {info.instance_id}")
                    await self._process_completed_instance(info)
                finally:
                    # Mark task as done
                    self._task_queue.task_done()
                    # Remove from processing set
                    self._processing_instances.discard(info.instance_id)

            except asyncio.CancelledError:
                logger.debug(f"Worker {worker_id} cancelled")
                break
            except Exception as e:
                logger.exception(f"[Worker {worker_id}] Unexpected error: {e}")

    async def _poll_and_enqueue(self) -> None:
        """
        Execute one poll and enqueue instances with status changes

        Detection logic:
        1. Query status='completed' AND last_polled_status='in_progress' AND callback_processed=FALSE
        2. Get the associated narrative_id
        3. Enqueue for processing
        """
        logger.debug(f"Polling for completed instances at {datetime.now()}")

        try:
            # 0. Low-cadence backstop: BLOCKED instances whose dependencies
            # already finished without this poller ever seeing the edge.
            now = datetime.now()
            last = self._last_blocked_reconcile
            if last is None or (now - last).total_seconds() >= _BLOCKED_RECONCILE_INTERVAL_S:
                self._last_blocked_reconcile = now
                await self._reconcile_blocked_instances()

            # 1. Query instances with status changes
            completed_instances = await self._find_completed_instances()

            if not completed_instances:
                logger.debug("No completed instances found")
                return

            # 2. Enqueue tasks (skip those already being processed)
            enqueued = 0
            for info in completed_instances:
                if info.instance_id not in self._processing_instances:
                    self._processing_instances.add(info.instance_id)
                    await self._task_queue.put(info)
                    enqueued += 1
                else:
                    logger.debug(f"Instance {info.instance_id} already processing, skipped")

            if enqueued > 0:
                logger.info(f"Enqueued {enqueued} instances (queue size: {self._task_queue.qsize()})")

        except Exception as e:
            logger.exception(f"Error in poll_and_enqueue: {e}")

    async def _reconcile_blocked_instances(self) -> int:
        """Activate narrative-less BLOCKED instances whose dependencies are all
        terminal but that no completion event ever unblocked (review I10).

        Narrative-bound BLOCKED instances are never candidates here
        (`get_blocked_page` excludes any instance with a narrative link,
        review r3 I2): `handle_completion`'s link-state rule owns them, and
        judging them by dependency status as well would be a second answer to
        the same question.

        Walks the WHOLE narrative-less BLOCKED set once per round, in keyset pages of
        `_BLOCKED_RECONCILE_PAGE` rows ordered by the auto-increment `id`
        (`InstanceRepository.get_blocked_page`). Each page is grouped by agent
        and handed, rows and all, to `InstanceHandler.reconcile_blocked_instances`
        — the SAME predicate and activation hook the narrative-free event path
        uses, so this cannot drift into a second definition of "dependencies
        satisfied".
        The handler does not re-query, so every query and every handler call
        is bounded by the page size; the round as a whole is not, on purpose.

        Why a full walk and not a fixed "oldest N" window (review r2 I-A): rows
        whose dependencies can never become terminal (upstream still ONGOING,
        or the dependency row deleted) stay BLOCKED forever. A fixed window
        ordered oldest-first fills up with them and every newer BLOCKED row is
        never looked at again — the backstop would fail silently. The cursor
        is the last `id` of the previous page and lives only inside this call:
        no process-local state carried between rounds, so a restarted or
        additional poller replica needs nothing handed over. (Two replicas
        reaching the same row in the same instant is the same pre-existing
        race the event path has with this backstop; nothing here widens it.)

        Returns the number of instances activated. Never raises into the loop.
        """
        try:
            from narranexus.platform.narrative import InstanceHandler

            repo = self._get_instance_repo()
            activated = 0
            agents: Set[str] = set()
            after_id = 0
            while True:
                rows = await repo.get_blocked_page(after_id, _BLOCKED_RECONCILE_PAGE)
                if not rows:
                    break
                by_agent: Dict[str, list] = {}
                for r in rows:
                    by_agent.setdefault(r.agent_id, []).append(r)
                for agent_id in sorted(by_agent):
                    handler = InstanceHandler(agent_id=agent_id)
                    handler.set_database_client(self.db)
                    newly = await handler.reconcile_blocked_instances(by_agent[agent_id])
                    if newly:
                        agents.add(agent_id)
                        activated += len(newly)
                # Terminate on an EMPTY page only; the cursor always advances.
                after_id = rows[-1].id
            if activated:
                logger.warning(
                    f"[blocked-reconcile] activated {activated} instance(s) across "
                    f"{len(agents)} agent(s) whose dependencies had already finished"
                )
            return activated
        except Exception as e:  # noqa: BLE001 — a backstop must never wedge the poll loop
            logger.exception(f"Error reconciling BLOCKED instances: {e}")
            return 0

    async def _find_completed_instances(self) -> List[CompletedInstanceInfo]:
        """
        Query instances with status changes

        Conditions (both halves):
        - status IN ('completed', 'failed')
        - last_polled_status = 'in_progress'
        - callback_processed = FALSE

        Two halves, each with its own LIMIT 100 (review r3 C1):
        - narrative-bound: INNER JOIN on an ACTIVE instance_narrative_links row;
          `narrative_id` is that link's narrative -> `handle_completion`.
        - narrative-less: instances with NO link row at all
          (`InstanceRepository.get_unlinked_completed_awaiting_callback`), e.g.
          every /api/jobs/complex Job; `narrative_id=None` ->
          `handle_completion_no_narrative`. Before this half existed they were
          never discovered, and their dependents waited for the 15-minute
          reconciliation. Kept a separate query so a backlog of these rows
          cannot push narrative completions out of the narrative window.
        An instance whose only links are HISTORY is in neither half (unchanged).

        Returns:
            List of CompletedInstanceInfo
        """
        result = []

        try:
            # Query instances with status changes
            query = """
                SELECT
                    mi.instance_id,
                    mi.agent_id,
                    mi.user_id,
                    mi.module_class,
                    mi.status,
                    inl.narrative_id
                FROM module_instances mi
                INNER JOIN instance_narrative_links inl
                    ON mi.instance_id = inl.instance_id
                WHERE mi.status IN ('completed', 'failed')
                    AND mi.last_polled_status = 'in_progress'
                    AND mi.callback_processed = FALSE
                    AND inl.link_type = 'active'
                ORDER BY mi.completed_at ASC
                LIMIT 100
            """

            rows = await self.db.execute(query, fetch=True)

            for row in rows:
                result.append(CompletedInstanceInfo(
                    instance_id=row["instance_id"],
                    narrative_id=row["narrative_id"],
                    agent_id=row["agent_id"],
                    user_id=row.get("user_id"),
                    module_class=row["module_class"],
                ))

        except Exception as e:
            logger.exception(f"Error finding completed instances: {e}")

        try:
            unlinked = await self._get_instance_repo().get_unlinked_completed_awaiting_callback(100)
            for inst in unlinked:
                result.append(CompletedInstanceInfo(
                    instance_id=inst.instance_id,
                    narrative_id=None,
                    agent_id=inst.agent_id,
                    user_id=inst.user_id,
                    module_class=inst.module_class,
                ))
        except Exception as e:
            logger.exception(f"Error finding completed narrative-less instances: {e}")

        return result

    # =========================================================================
    # Callback handling
    # =========================================================================

    async def _process_completed_instance(self, info: CompletedInstanceInfo) -> None:
        """
        Process a completed Instance

        Execution strategy: Path B (JobTrigger)
        - Only responsible for activating dependent instances
        - Does not directly trigger callback execution
        - Activated Jobs are executed via JobTrigger polling (next_run_time already set to NOW)

        Flow:
        1. Call InstanceHandler.handle_completion() to handle dependencies
        2. Record newly activated instances (JobTrigger is responsible for execution)
        3. Update callback_processed and last_polled_status

        Args:
            info: Completed Instance info
        """
        logger.info(f"Processing completed instance: {info.instance_id} ({info.module_class})")

        try:
            # 1. Get the current state of the instance
            instance_repo = self._get_instance_repo()
            instance = await instance_repo.get_by_instance_id(info.instance_id)

            if not instance:
                logger.warning(f"Instance {info.instance_id} not found")
                return

            # Determine final status
            status_str = instance.status if isinstance(instance.status, str) else instance.status.value
            new_status = InstanceStatus.COMPLETED if status_str == "completed" else InstanceStatus.FAILED

            # 2. Call InstanceHandler.handle_completion() to handle dependencies
            from narranexus.platform.narrative import InstanceHandler

            handler = InstanceHandler(agent_id=info.agent_id)
            handler.set_database_client(self.db)

            # B-16: a narrative-less instance (no instance_narrative_links row
            # at all, e.g. a Job created via /api/jobs/complex) is discovered by
            # the second half of `_find_completed_instances` with
            # narrative_id=None. handle_completion's narrative-scoped dependent
            # lookup can never see it, so resolve from
            # module_instances.dependencies instead.
            if info.narrative_id:
                newly_activated = await handler.handle_completion(
                    narrative_id=info.narrative_id,
                    instance_id=info.instance_id,
                    new_status=new_status,
                )
            else:
                newly_activated = await handler.handle_completion_no_narrative(
                    instance_id=info.instance_id,
                    new_status=new_status,
                )

            # 3. Record newly activated instances
            # Note: Using Path B strategy, these instances will be executed via JobTrigger polling
            # handle_completion has already set next_run_time = NOW() for JobModule instances
            if newly_activated:
                logger.info(f"Newly activated instances (will be executed by JobTrigger): {newly_activated}")
            else:
                logger.debug("No new instances activated")

            # 4. Update callback_processed and last_polled_status
            await self._mark_callback_processed(info.instance_id, status_str)

            logger.info(f"Instance {info.instance_id} processed successfully")

        except Exception as e:
            logger.exception(f"Error processing instance {info.instance_id}: {e}")
            # Mark as processed even on error to avoid infinite retries
            try:
                await self._mark_callback_processed(info.instance_id, "error")
            except Exception:
                pass

    async def _execute_callback(
        self,
        agent_id: str,
        user_id: Optional[str],
        narrative_id: str,
        instance_id: str,
        trigger_data: Dict[str, Any]
    ) -> None:
        """
        Trigger callback execution (background async)

        WARNING: Currently disabled (Path B strategy)
        This method is retained for future use when switching to Path A.

        Path A: ModulePoller directly calls this method to trigger AgentRuntime
        Path B (current): Relies on JobTrigger polling to execute activated Jobs

        Calls AgentRuntime._execute_callback_instance() to execute the newly activated instance

        Args:
            agent_id: Agent ID
            user_id: User ID
            narrative_id: Narrative ID
            instance_id: Newly activated Instance ID
            trigger_data: Trigger data
        """
        try:
            # Circuit-breaker gate. This is the ModulePoller's only path that
            # triggers AgentRuntime directly (Path A). It is currently DORMANT
            # — Path B (JobTrigger, which has its own breaker) is active — so
            # this gate is defensive: if Path A is ever switched on, a broken
            # agent (dead key / quota) won't be re-triggered here either.
            #
            # `peek_skip`, not the should_skip/try_begin_probe pair: this path
            # can never SETTLE a half-open probe (`_execute_callback_instance`
            # swallows every failure and reports no outcome), and a claim
            # nobody settles re-runs a dead credential every grant window
            # forever (#394 review C1). So it takes no probe and runs nothing
            # while the agent is PAUSED or PROBING at all — the probe is left
            # to the entry points that can settle it. Fail-open on read error.
            from narranexus.platform.agent_framework.loop.circuit_breaker import (
                peek_skip,
            )
            cb_held, cb_reason = await peek_skip(agent_id, db=await get_db_client())
            if cb_held:
                logger.info(
                    f"ModulePoller: skipping callback for instance {instance_id} "
                    f"— agent {agent_id} circuit-breaker open ({cb_reason})"
                )
                return

            # Lazy import to avoid circular dependencies
            from narranexus.platform.agent_runtime import AgentRuntime

            logger.info(f"Executing callback for instance: {instance_id}")

            runtime = AgentRuntime()
            await runtime._execute_callback_instance(
                narrative_id=narrative_id,
                instance_id=instance_id,
                trigger_data=trigger_data,
                agent_id=agent_id,
                user_id=user_id or "system",
            )

            logger.info(f"Callback executed for instance: {instance_id}")

        except Exception as e:
            logger.exception(f"Error executing callback for {instance_id}: {e}")

    async def _mark_callback_processed(self, instance_id: str, current_status: str) -> None:
        """
        Mark callback as processed

        Args:
            instance_id: Instance ID
            current_status: Current status
        """
        try:
            query = """
                UPDATE module_instances
                SET callback_processed = TRUE,
                    last_polled_status = %s,
                    updated_at = NOW()
                WHERE instance_id = %s
            """
            # fetch=False routes this UPDATE through execute_write, which the
            # SQLite proxy gates by transaction token and actually commits.
            # With fetch=True it would ride the read path (/execute), which
            # never commits and would fold into any open transaction.
            await self.db.execute(query, (current_status, instance_id), fetch=False)
            logger.debug(f"Marked callback processed: {instance_id}")
        except Exception as e:
            logger.exception(f"Error marking callback processed for {instance_id}: {e}")


# =============================================================================
# Process entry point
# =============================================================================

def run_module_poller(
    poll_interval: int = 5,
    max_workers: int = 3
) -> None:
    """
    Run ModulePoller (for standalone process invocation)

    Args:
        poll_interval: Polling interval (seconds)
        max_workers: Maximum concurrent worker count
    """
    import narranexus.platform.settings  # noqa: F401 - Ensure .env is loaded

    poller = ModulePoller(
        poll_interval=poll_interval,
        max_workers=max_workers
    )
    asyncio.run(poller.start())


# =============================================================================
# CLI entry point
# =============================================================================

def main():
    """CLI entry point for ModulePoller."""
    parser = argparse.ArgumentParser(
        description="ModulePoller - Module Callback Detection Service (Worker Pool mode)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start with default settings (5s interval, 3 workers)
  uv run python -m narranexus.platform.services.module_poller

  # Start with 10s interval and 5 workers
  uv run python -m narranexus.platform.services.module_poller --interval 10 --workers 5

  # Run once (for testing)
  uv run python -m narranexus.platform.services.module_poller --once
"""
    )

    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=5,
        help="Poll interval in seconds (default: 5)"
    )

    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=3,
        help="Max concurrent workers (default: 3)"
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
        "module_poller",
        level="DEBUG" if args.debug else None,
    )

    logger.info("ModulePoller - Module Callback Detection Service")
    logger.info(f"   Poll interval: {args.interval}s")
    logger.info(f"   Max workers: {args.workers}")
    logger.info(f"   Mode: {'Single run' if args.once else 'Continuous'}")
    logger.info("\n💡 Press Ctrl+C to stop\n")

    if args.once:
        # Run once for testing
        async def run_once():
            import narranexus.platform.settings  # noqa: F401
            poller = ModulePoller(
                poll_interval=args.interval,
                max_workers=args.workers
            )
            poller._db = await get_db_client()
            await poller._poll_and_enqueue()
            logger.info(f"\n✅ Single poll completed, {poller._task_queue.qsize()} instances in queue")

        asyncio.run(run_once())
    else:
        # Run continuously with Worker Pool
        run_module_poller(args.interval, args.workers)


if __name__ == "__main__":
    main()
