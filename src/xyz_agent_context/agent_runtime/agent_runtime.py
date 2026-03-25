"""
@file_name: agent_runtime.py
@author: NetMind.AI
@date: 2025-11-06
@description: Agent execution flow orchestrator

AgentRuntime is essentially an Orchestrator, responsible for coordinating the entire Agent execution flow.
It uses various services through dependency injection, keeping the orchestration logic clean.

Architecture:
- AgentRuntime is only responsible for flow orchestration (step sequence control)
- Specific work is delegated to injected services:
    - ExecutionState: State management
    - ResponseProcessor: Response processing
    - LoggingService: Log management
- The concrete implementation of each Step is in the _agent_runtime_steps/ directory
"""

from typing import Any, AsyncGenerator, Dict, Optional, Union
from loguru import logger

from xyz_agent_context.agent_runtime.cancellation import CancellationToken, CancelledByUser

# Type alias for database client
DatabaseClientType = Union["DatabaseClient", "AsyncDatabaseClient"]

# Schema - Runtime Messages
from xyz_agent_context.schema import (
    ProgressMessage,
    WorkingSource,
)

# Utils
from xyz_agent_context.utils import DatabaseClient, AsyncDatabaseClient

# Narrative
from xyz_agent_context.narrative import (
    EventService,
    NarrativeService,
    SessionService,
)

# Module
from xyz_agent_context.module import HookManager

# Extracted services
from xyz_agent_context.agent_runtime.response_processor import ResponseProcessor
from xyz_agent_context.agent_runtime.logging_service import LoggingService

# Step functions
from xyz_agent_context.agent_runtime._agent_runtime_steps import (
    RunContext,
    step_0_initialize,
    step_1_select_narrative,
    step_1_5_init_markdown,
    step_2_load_modules,
    step_2_5_sync_instances,
    step_3_execute_path,
    step_4_persist_results,
    step_5_execute_hooks,
)


class AgentRuntime:
    """
    Agent execution flow orchestrator

    Essentially an Orchestrator, responsible for coordinating the entire Agent execution flow (Steps 0-7).
    Accepts various services through dependency injection, keeping the orchestration logic clean.
    This class contains the runtime agent for the agent context module.

    Usage:
        # Using default services
        >>> runtime = AgentRuntime()
        >>> async for msg in runtime.run(agent_id, user_id, input_content, working_source):
        ...     print(msg)

        # Using custom services (for testing or special configuration)
        >>> runtime = AgentRuntime(
        ...     logging_service=LoggingService(log_dir="./custom_logs"),
        ...     response_processor=CustomResponseProcessor(),
        ... )

    Architecture (Plan B):
        AgentRuntime
            ├── EventService      - Event CRUD and intelligent selection
            ├── SessionService    - Session management (independent component)
            └── NarrativeService  - Narrative management
    """

    def __init__(
        self,
        database_client: Optional[DatabaseClientType] = None,
        logging_service: Optional[LoggingService] = None,
        response_processor: Optional[ResponseProcessor] = None,
        hook_manager: Optional[HookManager] = None,
        use_async_db: bool = True,
    ):
        """
        Initialize AgentRuntime

        Args:
            database_client: Database client (DatabaseClient or AsyncDatabaseClient).
                            If None, the type is determined by the use_async_db parameter.
            logging_service: Logging service, creates a new instance by default.
            response_processor: Response processor, creates a new instance by default.
            hook_manager: Hook manager, creates a new instance by default.
            use_async_db: Whether to use AsyncDatabaseClient (default True).
                            Only takes effect when database_client is None.
        """
        logger.info("="*80)
        logger.info("Initializing AgentRuntime")

        # Database client (may require lazy initialization)
        self._database_client = database_client
        self._use_async_db = use_async_db
        self._owns_db_client = database_client is None  # Flag indicating whether we need to close it ourselves

        # Injected services (dependency injection, optional parameters)
        self._logging_service = logging_service or LoggingService()
        self._response_processor = response_processor or ResponseProcessor()
        self.hook_manager = hook_manager or HookManager()

        # Managers created at runtime
        self.agent_hooks = []
        self._log_handler_id = None  # Used to store the log file handler ID

        # The three major Managers will be created in the run() method based on agent_id/user_id
        self.event_service = None
        self.session_service = None
        self.narrative_service = None

        # Current running agent_id and user_id (used for callbacks)
        self._current_agent_id = None
        self._current_user_id = None

        logger.info("AgentRuntime initialized successfully")
        logger.info("="*80)

    async def run(
        self,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Union[WorkingSource, str] = WorkingSource.CHAT,
        pass_mcp_urls: dict = {},
        job_instance_id: Optional[str] = None,
        forced_narrative_id: Optional[str] = None,
        trigger_extra_data: Optional[Dict[str, Any]] = None,
        cancellation: Optional[CancellationToken] = None,
    ) -> AsyncGenerator:
        """
        Execute the main flow of the Agent runtime

        Overall flow:
        ```
        ┌─────────────────────────────────────────────────────────────────────────────┐
        │                           AgentRuntime.run() Flow                            │
        ├─────────────────────────────────────────────────────────────────────────────┤
        │                                                                             │
        │  [Initialization Phase]                                                      │
        │    Step 0:   Initialize (get config, create Event, init Session)             │
        │                                                                             │
        │  [Context Preparation Phase]                                                 │
        │    Step 1:   Select Narrative (retrieve or create the storyline)             │
        │    Step 1.5: Initialize Markdown (read historical conversation records)      │
        │                                                                             │
        │  [Module Loading Phase]                                                      │
        │    Step 2:   Load Modules and decide execution path                         │
        │              - Use LLM to decide which Module Instances are needed          │
        │              - Decide execution path: AGENT_LOOP or DIRECT_TRIGGER          │
        │    Step 2.5: Sync Instances (update Markdown + sync to database)            │
        │                                                                             │
        │  [Execution Phase]                                                           │
        │    Step 3:   Execute path (based on Step 2 decision)                        │
        │              - AGENT_LOOP: Call Agent Loop for LLM reasoning                │
        │              - DIRECT_TRIGGER: Directly call MCP Tool                       │
        │                                                                             │
        │  [Persistence Phase]                                                         │
        │    Step 4:   Persist results (Trajectory + stats + Event/Narratives)        │
        │                                                                             │
        │  [Post-processing Phase]                                                     │
        │    Step 5:   Execute Hooks (each Module's after_event_execution)            │
        │    Step 6:   Process Hook Callbacks (handle newly triggered Instances)       │
        │                                                                             │
        └─────────────────────────────────────────────────────────────────────────────┘
        ```

        Args:
            agent_id: Agent unique identifier
            user_id: User unique identifier
            input_content: User input content
            working_source: Working source identifier (WorkingSource enum or string)
            pass_mcp_urls: Externally provided MCP Server URLs
            job_instance_id: Instance ID when executing a Job
            forced_narrative_id: Forced Narrative ID (used for Job triggers, skips Narrative selection)
            trigger_extra_data: Trigger 层传入的附加数据（如 channel_tag），会合并到 ctx_data.extra_data

        Yields:
            ProgressMessage: Progress messages for each step
            AgentTextDelta: Agent text output deltas
        """
        # =============================================================================
        # Initialization
        # =============================================================================
        self._logging_service.setup(agent_id)

        # Ensure database client is initialized (lazy-load AsyncDatabaseClient)
        db_client = await self._ensure_database_client()

        # Override user_id with agent's creator — all triggers share a single workspace
        # so that Matrix conversations, Job triggers, etc. see the same narratives/jobs.
        from xyz_agent_context.repository.agent_repository import AgentRepository
        _agent = await AgentRepository(db_client).get_agent(agent_id)
        if _agent and _agent.created_by:
            original_user_id = user_id
            user_id = _agent.created_by
            if original_user_id != user_id:
                logger.info(f"user_id overridden: {original_user_id} -> {user_id} (agent creator)")

        # Save current running agent_id and user_id (used for callbacks)
        self._current_agent_id = agent_id
        self._current_user_id = user_id

        # Set global cost tracking context so ALL LLM calls (narrative, job, social
        # network, module decisions, etc.) automatically record costs without needing
        # explicit agent_id/db parameters at each call site.
        from xyz_agent_context.utils.cost_tracker import set_cost_context, clear_cost_context
        set_cost_context(agent_id, db_client)

        # Initialize the three major Services
        self.session_service = SessionService()
        self.event_service = EventService(agent_id)
        self.narrative_service = NarrativeService(agent_id)
        # Inject EventService into NarrativeService (used for generating summaries during updates)
        self.narrative_service.set_event_service(self.event_service)

        # Initialize Markdown and Trajectory managers
        from xyz_agent_context.narrative import NarrativeMarkdownManager, TrajectoryRecorder
        self.markdown_manager = NarrativeMarkdownManager(agent_id, user_id)
        self.trajectory_recorder = TrajectoryRecorder(agent_id, user_id)

        logger.info("\n" + "="*80)
        logger.info("🚀 AgentRuntime.run() started")
        logger.info(f"📋 Parameters: agent_id={agent_id}, user_id={user_id}")
        logger.info(f"💬 Input content: {input_content}")
        logger.info("="*80)

        # =============================================================================
        # Create run context
        # =============================================================================
        # Use a no-op token if none provided (avoids None checks everywhere)
        if cancellation is None:
            cancellation = CancellationToken()

        ctx = RunContext(
            agent_id=agent_id,
            user_id=user_id,
            input_content=input_content,
            working_source=working_source,
            pass_mcp_urls=pass_mcp_urls,
            job_instance_id=job_instance_id,
            forced_narrative_id=forced_narrative_id,
            trigger_extra_data=trigger_extra_data or {},
            cancellation=cancellation,
        )

        # =============================================================================
        # Step 0: Initialization
        # =============================================================================
        # [Function] Execute all initialization work
        #
        # [Internal Logic]
        #   0.1 Get Agent configuration (load from database)
        #   0.2 Initialize ModuleService (prepare module loader)
        #   0.3 Create Event record (carrier for this conversation)
        #   0.4 Get/Create Session (manage session continuity)
        #
        # [Output]
        #   - ctx.agent_data: Agent configuration info
        #   - ctx.module_service: ModuleService instance
        #   - ctx.event: Event record
        #   - ctx.session: Session object
        #   - ctx.awareness: Agent self-awareness content
        # =============================================================================
        async for msg in step_0_initialize(
            ctx, db_client, self.event_service, self.session_service
        ):
            yield msg

        # =============================================================================
        # Step 1: Select Narrative
        # =============================================================================
        # [Function] Retrieve or create the corresponding Narrative (storyline/topic)
        #
        # [Internal Logic]
        #   ┌─────────────────────────────────────────────────────────┐
        #   │  1. Detect Narrative ownership (ContinuityDetector)     │
        #   │     - Compare current Query with session.last_query     │
        #   │     - Load current Narrative info (name, desc, summary, │
        #   │       keywords)                                         │
        #   │     - Use LLM to determine if it belongs to current     │
        #   │       Narrative                                         │
        #   │     - Note: conversation continuity != same Narrative   │
        #   │                                                         │
        #   │  2. Branch based on ownership result:                   │
        #   │     ├─ Belongs to current Narrative -> reuse            │
        #   │     │  session.current_narrative_id                     │
        #   │     │                                                   │
        #   │     └─ Does not belong -> search for matching Narrative │
        #   │        a. Generate Query embedding                      │
        #   │        b. Vector search for similar Narratives          │
        #   │        c. Score > threshold -> reuse existing Narrative │
        #   │        d. Score < threshold -> create new Narrative     │
        #   │                                                         │
        #   │  3. Update Session.current_narrative_id                 │
        #   └─────────────────────────────────────────────────────────┘
        #
        # [Output]
        #   - ctx.narrative_list: List[Narrative] (may match multiple)
        #   - ctx.main_narrative: Narrative (the primary one)
        # =============================================================================
        async for msg in step_1_select_narrative(
            ctx, self.narrative_service, self.session_service
        ):
            yield msg

        # =============================================================================
        # Step 1.5: Initialize/Read Markdown History
        # =============================================================================
        # [Function] Read the Markdown file corresponding to the Narrative, get historical context
        #
        # [Internal Logic]
        #   1. Build file path based on narrative_id
        #   2. If file exists -> read content
        #   3. If not exists -> create empty file
        #   4. Parse historical conversations and Instance info from Markdown
        #
        # [Output] ctx.markdown_history: str
        #   - Contains historical conversation records
        #   - Contains Instance state information
        #   - Will be used as part of the LLM context
        # =============================================================================
        await step_1_5_init_markdown(ctx, self.markdown_manager)

        # =============================================================================
        # Step 2: Load Modules and decide execution path -- Core Step
        # =============================================================================
        # [Function] Use LLM to intelligently decide which Module Instances are needed,
        #            and the execution path
        #
        # [Internal Logic]
        #   ┌─────────────────────────────────────────────────────────┐
        #   │  1. Collect context information                         │
        #   │     - user_input: User input                            │
        #   │     - current_instances: Narrative's active_instances   │
        #   │     - narrative_summary: Narrative summary              │
        #   │     - markdown_history: Historical conversations        │
        #   │                                                         │
        #   │  2. Call llm_decide_instances()                         │
        #   │     - Build Prompt (with Module metadata and rules)     │
        #   │     - Call LLM (using Structured Output)                │
        #   │     - Parse returned InstanceDecisionOutput             │
        #   │                                                         │
        #   │  3. Instantiate Modules based on decision results       │
        #   │     - Iterate over active_instances                     │
        #   │     - Create corresponding Module object for each inst  │
        #   │     - Bind instance.module = Module instance            │
        #   │                                                         │
        #   │  4. Start MCP Servers (if Module requires)              │
        #   └─────────────────────────────────────────────────────────┘
        #
        # [Execution Path Decision]
        #   - AGENT_LOOP (99%): Normal conversation/Q&A/needs LLM reasoning
        #   - DIRECT_TRIGGER (1%): Explicit API call (e.g., "send message to XX")
        #
        # [Output]
        #   - ctx.load_result: ModuleLoadResult
        #     - execution_path: "agent_loop" | "direct_trigger"
        #     - active_instances: List[ModuleInstance]
        #     - direct_trigger: DirectTriggerConfig (if applicable)
        #     - relationship_graph: str (Mermaid format)
        #   - ctx.active_instances: List[ModuleInstance]
        #   - ctx.module_list: List[Module] (instantiated Module objects)
        # =============================================================================
        async for msg in step_2_load_modules(ctx):
            yield msg

        # ---- Cancellation checkpoint (before expensive Step 2.5) ----
        cancellation.raise_if_cancelled()

        # =============================================================================
        # Step 2.5: Sync Instance Changes
        # =============================================================================
        # [Function] Update Markdown and sync Instances to database
        #
        # [Internal Logic]
        #   2.5.1 Update Markdown (Instances and relationship graph)
        #   2.5.2 Sync Instance changes to database
        #         - Added Instances: establish associations
        #         - Removed Instances: remove associations
        #         - Updated Instances: update status
        #
        # [Output] Update Markdown and database
        # =============================================================================
        async for msg in step_2_5_sync_instances(
            ctx, self.narrative_service, self.markdown_manager
        ):
            yield msg

        # ---- Cancellation checkpoint (before the main LLM call) ----
        cancellation.raise_if_cancelled()

        # =============================================================================
        # Step 3: Execute different paths based on execution_path -- Core Step
        # =============================================================================
        # [Function] Based on Step 2 decision, execute AGENT_LOOP or DIRECT_TRIGGER
        #
        # [Internal Logic - AGENT_LOOP Path]
        #   ┌─────────────────────────────────────────────────────────┐
        #   │  1. Data gathering phase (hook_data_gathering)          │
        #   │     - Iterate all active Modules                        │
        #   │     - Call each Module's hook_data_gathering()           │
        #   │     - Collect each Module's ctx_data (e.g. SocialNet)   │
        #   │                                                         │
        #   │  2. Context merging phase                               │
        #   │     - Merge all Module Instructions                     │
        #   │     - Collect all Module MCP Server URLs                │
        #   │     - Merge ctx_data into unified context               │
        #   │                                                         │
        #   │  3. Agent Loop execution                                │
        #   │     - Build System Prompt (Agent Config + Instructions) │
        #   │     - Connect MCP Servers                               │
        #   │     - Call LLM (supports multi-turn Tool calls)         │
        #   │     - Stream output AgentTextDelta                      │
        #   │                                                         │
        #   │  4. Result processing                                   │
        #   │     - Collect final_output                              │
        #   │     - Record execution_steps                            │
        #   └─────────────────────────────────────────────────────────┘
        #
        # [Internal Logic - DIRECT_TRIGGER Path]
        #   ┌─────────────────────────────────────────────────────────┐
        #   │  1. Parse direct_trigger config                         │
        #   │     - module_class: Target Module                       │
        #   │     - trigger_name: MCP Tool name                       │
        #   │     - params: Call parameters                           │
        #   │                                                         │
        #   │  2. Find corresponding Module's MCP Server URL          │
        #   │                                                         │
        #   │  3. Directly call MCP Tool (skip LLM)                   │
        #   │                                                         │
        #   │  4. Return execution result                             │
        #   └─────────────────────────────────────────────────────────┘
        #
        # [Output] ctx.execution_result: PathExecutionResult
        #   - final_output: Final output content
        #   - execution_steps: List of execution steps
        #   - agent_loop_response: Raw response from Agent Loop
        # =============================================================================
        async for msg in step_3_execute_path(ctx, db_client, self._response_processor):
            yield msg
            # Check cancellation after each streamed message from the agent loop
            if cancellation.is_cancelled:
                logger.info("Cancellation detected during Step 3 (agent loop), breaking")
                break

        # ---- Cancellation checkpoint (before persistence) ----
        cancellation.raise_if_cancelled()

        # =============================================================================
        # Step 4: Persist Execution Results
        # =============================================================================
        # [Function] Save execution results to various storages
        #
        # [Internal Logic]
        #   ┌─────────────────────────────────────────────────────────┐
        #   │  4.1 Record Trajectory (execution trace file)           │
        #   │                                                         │
        #   │  4.2 Update Markdown statistics                         │
        #   │                                                         │
        #   │  4.3 Update Event                                       │
        #   │     - Set final_output                                  │
        #   │     - Set event_log (execution log)                     │
        #   │     - Set module_instances                              │
        #   │                                                         │
        #   │  4.4 Update Narratives                                  │
        #   │     - Add event_id to event_ids list                    │
        #   │     - Update dynamic_summary (LLM-generated summary)   │
        #   └─────────────────────────────────────────────────────────┘
        #
        # [Output] Update files and database
        # =============================================================================
        async for msg in step_4_persist_results(
            ctx,
            self.event_service,
            self.narrative_service,
            self.markdown_manager,
            self.trajectory_recorder,
            self.session_service
        ):
            yield msg

        # =============================================================================
        # Step 5: Execute Hooks
        # =============================================================================
        # [Function] Call each Module's hook_after_event_execution
        #
        # [Internal Logic]
        #   ┌─────────────────────────────────────────────────────────┐
        #   │  1. Build HookAfterExecutionParams                      │
        #   │     - execution_ctx: event_id, agent_id, user_id        │
        #   │     - io_data: input_content, final_output              │
        #   │     - trace: event_log, agent_loop_response             │
        #   │                                                         │
        #   │  2. Iterate all active Modules                          │
        #   │     - Call module.hook_after_event_execution(params)     │
        #   │     - E.g.: SocialNetworkModule updates entity info     │
        #   │                                                         │
        #   │  3. Collect callback requests                           │
        #   │     - Check for Instance state changes                  │
        #   │     - Record dependent Instances that need triggering   │
        #   └─────────────────────────────────────────────────────────┘
        #
        # [Output] hook_callback_results: callback requests for subsequent processing
        # =============================================================================
        # =============================================================================
        # Steps 5 + 6: Execute Hooks & Process Callbacks (BACKGROUND)
        # =============================================================================
        # Move to background so the WebSocket can close immediately after Step 4.
        # The user already saw the full response during Step 3; Steps 5-6 are
        # post-processing (entity extraction, memory writes, job analysis, etc.)
        # that should not block the UI.
        #
        # Safety: Step 4.3 already persisted ctx.event.final_output, so hooks
        # have access to the response text. Modules use the global shared DB
        # client from db_factory (not the runtime's own client), so DB access
        # survives after AgentRuntime.__aexit__ closes its connection.
        # =============================================================================
        import asyncio
        import time as _time

        _bg_start = _time.monotonic()
        _agent_id = ctx.agent_id
        _logging_service = self._logging_service  # capture ref for background task

        async def _run_hooks_background():
            """Run Step 5 hooks + Step 6 callbacks in background."""
            try:
                hook_callback_results = None
                async for msg in step_5_execute_hooks(ctx, self.hook_manager):
                    if isinstance(msg, ProgressMessage):
                        pass  # No WebSocket to send to; just skip progress messages
                    else:
                        hook_callback_results = msg

                # Step 6: Process Hook Callbacks
                if hook_callback_results:
                    await self.hook_manager.hook_callback_results(
                        hook_callback_results=hook_callback_results,
                        narrative=ctx.main_narrative,
                        narrative_service=self.narrative_service,
                        execute_callback_instance=self._execute_callback_instance
                    )

                elapsed = _time.monotonic() - _bg_start
                logger.info(
                    f"[BG] Steps 5-6 completed for {_agent_id} in {elapsed:.1f}s"
                )
            except Exception as e:
                elapsed = _time.monotonic() - _bg_start
                logger.error(
                    f"[BG] Steps 5-6 failed for {_agent_id} after {elapsed:.1f}s: {e}"
                )
            finally:
                clear_cost_context()
                # Clean up the agent log file handler AFTER background work finishes,
                # so all [BG] log lines are captured in the agent's .log file.
                _logging_service.cleanup()

        asyncio.create_task(_run_hooks_background())
        logger.info(f"[BG] Steps 5-6 dispatched to background for {_agent_id}")

        # Yield a completed Step 5 progress message so the frontend sidebar
        # shows "Post-processing (background) ✓" without actually waiting.
        yield ProgressMessage(
            step="5",
            title="Post-processing (background)",
            description="✓ Module hooks dispatched to background",
            status=ProgressStatus.COMPLETED,
            substeps=["Entity extraction, memory writes, job analysis running in background"],
        )

        # NOTE: Do NOT call self._logging_service.cleanup() here.
        # The background task owns the cleanup — it will remove the log
        # handler after hooks finish so that [BG] lines go to the agent's log file.

    async def _execute_callback_instance(
        self,
        narrative_id: str,
        instance_id: str,
        trigger_data: Optional[Dict] = None,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> None:
        """
        Execute a newly activated instance in the background (CALLBACK trigger)

        This method runs asynchronously in the background without blocking the main flow.
        Used to handle instances that are automatically activated after dependencies complete.

        Args:
            narrative_id: Narrative ID
            instance_id: Newly activated instance ID
            trigger_data: Data passed from the preceding instance
            agent_id: Agent ID (optional, defaults to self._current_agent_id)
            user_id: User ID (optional, defaults to self._current_user_id)
        """
        # Prefer passed-in parameters, otherwise use current context
        effective_agent_id = agent_id or self._current_agent_id
        effective_user_id = user_id or self._current_user_id

        if not effective_agent_id:
            logger.error(f"❌ [Background] No agent_id available for instance: {instance_id}")
            return

        logger.info(f"🔄 [Background] Executing callback instance: {instance_id}")

        try:
            # Build input content for the Callback Trigger
            input_content = f"[CALLBACK] Instance {instance_id} activated"
            if trigger_data:
                input_content += f" with data: {trigger_data}"

            # Execute AgentRuntime in background, using CALLBACK working_source
            async for msg in self.run(
                agent_id=effective_agent_id,
                user_id=effective_user_id,
                input_content=input_content,
                working_source=WorkingSource.CALLBACK  # Mark as callback trigger
            ):
                # Background execution, do not process output (or log it)
                if hasattr(msg, 'title'):
                    logger.debug(f"  [Background] {msg.title}")

            logger.info(f"✅ [Background] Instance {instance_id} execution completed")

        except Exception as e:
            logger.error(f"❌ [Background] Instance {instance_id} execution failed: {e}")
            # Can record the error to database or send notifications here

    async def _ensure_database_client(self) -> DatabaseClientType:
        """
        Ensure database client is initialized (lazy loading)

        Returns:
            DatabaseClient or AsyncDatabaseClient instance
        """
        if self._database_client is None:
            # Use the globally shared AsyncDatabaseClient (singleton pattern)
            from xyz_agent_context.utils.db_factory import get_db_client
            logger.info("Getting shared AsyncDatabaseClient from db_factory")
            self._database_client = await get_db_client()
        return self._database_client

    @property
    def database_client(self) -> Optional[DatabaseClientType]:
        """Get database client (may be None if not yet initialized)"""
        return self._database_client

    async def cleanup(self) -> None:
        """
        Clean up AgentRuntime resources

        Main cleanup:
        - AsyncDatabaseClient connection pool (if we created it)
        """
        if self._owns_db_client and self._database_client is not None:
            if isinstance(self._database_client, AsyncDatabaseClient):
                logger.info("Closing AsyncDatabaseClient connection pool")
                await self._database_client.close()
            self._database_client = None

    async def __aenter__(self) -> "AgentRuntime":
        """Support async with syntax"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Automatically clean up resources on exit"""
        await self.cleanup()


async def test_agent_runtime():
    # Use async with for automatic resource management
    async with AgentRuntime() as agent_runtime:
        async for response in agent_runtime.run(
            agent_id="agent_ecb12faf",
            user_id="user_binliang",
            input_content="Do you know what a vector bundle is?",
            working_source=WorkingSource.CHAT,  # Use enum type (also supports string "chat")
        ):
            logger.info(f"response: {response}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_agent_runtime())
