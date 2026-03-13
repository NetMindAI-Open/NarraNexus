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
    uv run python -m xyz_agent_context.services.module_poller

    # Custom parameters
    uv run python -m xyz_agent_context.services.module_poller --interval 10 --workers 3
"""

import asyncio
import argparse
from datetime import datetime
from typing import List, Optional, Dict, Any, Set
from dataclasses import dataclass

from loguru import logger

# Schema
from xyz_agent_context.schema.instance_schema import (
    ModuleInstanceRecord,
    InstanceStatus,
    LinkType,
)

# Utils
from xyz_agent_context.utils import AsyncDatabaseClient, get_db_client

# Repository
from xyz_agent_context.repository import (
    InstanceRepository,
    InstanceNarrativeLinkRepository,
)


@dataclass
class CompletedInstanceInfo:
    """Completed Instance info (used for queue passing)"""
    instance_id: str
    narrative_id: str
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

        logger.info("=" * 60)
        logger.info("🔄 ModulePoller starting (Worker Pool mode)...")
        logger.info(f"   Poll interval: {self.poll_interval} seconds")
        logger.info(f"   Max workers: {self.max_workers}")
        logger.info("=" * 60)

        self.running = True

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
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                logger.debug("Poller cancelled")
                break
            except Exception as e:
                logger.error(f"Poller error: {e}")
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
                logger.error(f"[Worker {worker_id}] Unexpected error: {e}")

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
            logger.error(f"Error in poll_and_enqueue: {e}")

    async def _find_completed_instances(self) -> List[CompletedInstanceInfo]:
        """
        Query instances with status changes

        Conditions:
        - status IN ('completed', 'failed')
        - last_polled_status = 'in_progress'
        - callback_processed = FALSE

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
            logger.error(f"Error finding completed instances: {e}")

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
            from xyz_agent_context.narrative import InstanceHandler

            handler = InstanceHandler(agent_id=info.agent_id)
            handler.set_database_client(self.db)

            newly_activated = await handler.handle_completion(
                narrative_id=info.narrative_id,
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

            logger.success(f"Instance {info.instance_id} processed successfully")

        except Exception as e:
            logger.error(f"Error processing instance {info.instance_id}: {e}")
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
            # Lazy import to avoid circular dependencies
            from xyz_agent_context.agent_runtime import AgentRuntime

            logger.info(f"Executing callback for instance: {instance_id}")

            runtime = AgentRuntime()
            await runtime._execute_callback_instance(
                narrative_id=narrative_id,
                instance_id=instance_id,
                trigger_data=trigger_data,
                agent_id=agent_id,
                user_id=user_id or "system",
            )

            logger.success(f"Callback executed for instance: {instance_id}")

        except Exception as e:
            logger.error(f"Error executing callback for {instance_id}: {e}")

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
            await self.db.execute(query, (current_status, instance_id))
            logger.debug(f"Marked callback processed: {instance_id}")
        except Exception as e:
            logger.error(f"Error marking callback processed for {instance_id}: {e}")


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
    import xyz_agent_context.settings  # noqa: F401 - Ensure .env is loaded

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
  uv run python -m xyz_agent_context.services.module_poller

  # Start with 10s interval and 5 workers
  uv run python -m xyz_agent_context.services.module_poller --interval 10 --workers 5

  # Run once (for testing)
  uv run python -m xyz_agent_context.services.module_poller --once
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

    # Configure logging level
    if args.debug:
        import sys
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    from xyz_agent_context.utils.service_logger import setup_service_logger
    setup_service_logger("module_poller")

    logger.info("=" * 60)
    logger.info("ModulePoller - Module Callback Detection Service")
    logger.info("=" * 60)
    logger.info(f"   Poll interval: {args.interval}s")
    logger.info(f"   Max workers: {args.workers}")
    logger.info(f"   Mode: {'Single run' if args.once else 'Continuous'}")
    logger.info("=" * 60)
    logger.info("\n💡 Press Ctrl+C to stop\n")

    if args.once:
        # Run once for testing
        async def run_once():
            import xyz_agent_context.settings  # noqa: F401
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
