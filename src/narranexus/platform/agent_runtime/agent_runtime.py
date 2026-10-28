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
- File logging is owned by utils.logging.setup_logging(), called once
  per process at startup. AgentRuntime does NOT manage per-run sinks
  any more — the previous LoggingService leaked file descriptors on
  EC2 (see M4 / T15 in the log-system overhaul) and was removed.
- The concrete implementation of each Step is in the _agent_runtime_steps/ directory
"""

import asyncio
from contextlib import ExitStack
from typing import Any, AsyncGenerator, Dict, Optional, Union
from uuid import uuid4
from loguru import logger

from narranexus.platform.agent_runtime.cancellation import CancellationToken, CancelledByUser
from narranexus.platform.utils.logging import bind_event

# Type alias for database client
DatabaseClientType = Union["DatabaseClient", "AsyncDatabaseClient"]

# Schema - Runtime Messages
from narranexus.platform.schema import (
    ProgressMessage,
    ProgressStatus,
    WorkingSource,
)
from narranexus.platform.schema.turn_profile import TurnProfile

# Utils
from narranexus.platform.utils import DatabaseClient, AsyncDatabaseClient

# Narrative
from narranexus.platform.narrative import (
    EventService,
    NarrativeService,
    SessionService,
)

# Module
from narranexus.platform.module_system import HookManager

# Extracted services
from narranexus.platform.agent_runtime.response_processor import ResponseProcessor

# The step bodies live behind the public ``agent_runtime.steps`` surface; the
# runtime itself only needs the context type (the stage strategies in
# plugins/builtin.turn call the steps).
from narranexus.platform.agent_runtime.steps import RunContext

def _resolve_turn_profile(
    fast_mode: bool,
    turn_profile: Optional["TurnProfile"],
    working_source: Union[WorkingSource, str],
) -> Optional["TurnProfile"]:
    """Resolve the trigger-facing ``fast_mode`` boolean into a TurnProfile.

    Policy lives here so triggers only signal intent with one boolean. An
    explicit profile always wins — paths that build their own (e.g. the
    voice path's ``TurnProfile.voice_fast()``) are unaffected by the flag.
    Pure — unit-tested in test_resolve_turn_profile.py.
    """
    if turn_profile is not None:
        if fast_mode:
            logger.debug(
                "fast_mode ignored: explicit turn_profile '{}' takes precedence",
                turn_profile.name,
            )
        return turn_profile
    if fast_mode:
        return TurnProfile.fast_for(working_source)
    return None


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
        response_processor: Optional[ResponseProcessor] = None,
        hook_manager: Optional[HookManager] = None,
        use_async_db: bool = True,
        registries: Optional[Any] = None,
    ):
        """
        Initialize AgentRuntime

        Args:
            database_client: Database client (DatabaseClient or AsyncDatabaseClient).
                            If None, the type is determined by the use_async_db parameter.
            response_processor: Response processor, creates a new instance by default.
            hook_manager: Hook manager, creates a new instance by default.
            use_async_db: Whether to use AsyncDatabaseClient (default True).
                            Only takes effect when database_client is None.
        """
        logger.info("Initializing AgentRuntime")

        # Plugin registries the turn pipeline reads strategies/profiles/hooks from
        # (None = the process registries; tests inject a private Registries).
        self._registries = registries

        # Database client (may require lazy initialization)
        self._database_client = database_client
        self._use_async_db = use_async_db
        self._owns_db_client = database_client is None  # Flag indicating whether we need to close it ourselves

        # Injected services (dependency injection, optional parameters)
        self._response_processor = response_processor or ResponseProcessor()
        self.hook_manager = hook_manager or HookManager()

        # Managers created at runtime
        self.agent_hooks = []

        # The three major Managers will be created in the run() method based on agent_id/user_id
        self.event_service = None
        self.session_service = None
        self.narrative_service = None

        # Current running agent_id and user_id (used for callbacks)
        self._current_agent_id = None
        self._current_user_id = None

        logger.info("AgentRuntime initialized successfully")
    async def run(
        self,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Union[WorkingSource, str] = WorkingSource.CHAT,
        pass_mcp_servers: dict = {},
        job_instance_id: Optional[str] = None,
        forced_narrative_id: Optional[str] = None,
        trigger_extra_data: Optional[Dict[str, Any]] = None,
        cancellation: Optional[CancellationToken] = None,
        silent: bool = False,
        fast_mode: bool = False,
        turn_profile: Optional["TurnProfile"] = None,
        steering: Optional[Any] = None,
        pipeline_profile: Optional[str] = None,
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
            pass_mcp_servers: Externally provided MCP server specs ({name: {"url", "headers"?}})
            job_instance_id: Instance ID when executing a Job
            forced_narrative_id: Forced Narrative ID (used for Job triggers, skips Narrative selection)
            trigger_extra_data: Trigger 层传入的附加数据（如 channel_tag），会合并到 ctx_data.extra_data
            silent: When True, skip step_3 (agent LLM invocation) entirely.
                The run still executes step_0..step_2.5 (event created, narrative
                selected, modules loaded, instances synced), then constructs a
                minimal empty PathExecutionResult so step_4 / persist_turn /
                step_5 can run against a consistent ctx. Used by IM triggers for
                group non-@ messages (or reconnect burst backfill): the agent
                does not reply, but ChatModule still writes conversation history,
                GeneralMemoryModule still extracts observations, and
                SocialNetworkModule still updates entity descriptions.
                Batch metadata (per-message sender_id/timestamp) travels in
                trigger_extra_data["batch_messages"]; ChatModule branches to
                a batch write path when present. Default False = existing
                owner-facing behavior, byte-identical.

        Yields:
            ProgressMessage: Progress messages for each step
            AgentTextDelta: Agent text output deltas
        """
        # =============================================================================
        # Initialization
        # =============================================================================
        # ------------- Trace context injection (M1/T4) -------------
        # Generate a short run_id for THIS invocation and bind run-wide
        # fields (run_id / agent_id / user_id / optional trigger_id) onto
        # loguru's contextvar so every downstream log line can be linked
        # to one user message. event_id is bound below once Step 0 has
        # created the Event row.
        # Turn phase timing ([turn-timing], 2026-08-05): stamps that split a
        # turn into pre (this point -> Step 0: lazy DB client, agent lookup,
        # service construction — pool contention lands HERE) / setup (Steps
        # 0-2.5) / loop (Step 3) / persist (Steps 4+4.6). The 2026-08-01
        # event measured "one reply = 1-7 minutes" with no way to say WHICH
        # phase ate it — this is the measurement that decides whether any
        # latency work is worth doing (Base recvrdLPavdQgU).
        import time as _time
        _t_run_start = _time.monotonic()

        run_id = f"run_{uuid4().hex[:8]}"
        _trigger_id = (trigger_extra_data or {}).get("trigger_id")
        _bind_kwargs: dict[str, str] = {
            "run_id": run_id,
            "agent_id": str(agent_id),
            "user_id": str(user_id),
        }
        if _trigger_id is not None:
            _bind_kwargs["trigger_id"] = str(_trigger_id)
        with ExitStack() as _trace_stack:
            _trace_stack.enter_context(bind_event(**_bind_kwargs))

            # Ensure database client is initialized (lazy-load AsyncDatabaseClient)
            db_client = await self._ensure_database_client()

            # Override user_id with agent's creator — all triggers share a single workspace
            # so that Lark conversations, Job triggers, etc. see the same narratives/jobs.
            from narranexus.platform.repository.agent_repository import AgentRepository
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
            # Scope, not a bare set: run() is NOT always the outermost frame.
            # A Step-6 callback can drive a nested run() (_execute_callback_
            # instance), which would otherwise overwrite the parent's pair for
            # good — today both frames carry the same agent_id and db singleton
            # so nothing shows, but the first caller to pass a different
            # agent_id would silently book the parent's remaining post-turn
            # spend against the child. Unwinds on the same ExitStack as the
            # event scope below.
            from narranexus.platform.utils.cost_tracker import (
                clear_cost_context,
                cost_context_scope,
                cost_event_scope,
            )
            _trace_stack.enter_context(cost_context_scope(agent_id, db_client))

            # Initialize the three major Services
            self.session_service = SessionService()
            self.event_service = EventService(agent_id)
            self.narrative_service = NarrativeService(agent_id)
            # Inject EventService into NarrativeService (used for generating summaries during updates)
            self.narrative_service.set_event_service(self.event_service)

            # Initialize Markdown and Trajectory managers
            from narranexus.platform.narrative import NarrativeMarkdownManager, TrajectoryRecorder
            self.markdown_manager = NarrativeMarkdownManager(agent_id, user_id)
            self.trajectory_recorder = TrajectoryRecorder(agent_id, user_id)

            logger.info("AgentRuntime.run() started")
            logger.info(f"Parameters: agent_id={agent_id}, user_id={user_id}")
            logger.info(f"Input content: {input_content}")
            # =============================================================================
            # Create run context
            # =============================================================================
            # Use a no-op token if none provided (avoids None checks everywhere)
            if cancellation is None:
                cancellation = CancellationToken()

            turn_profile = _resolve_turn_profile(
                fast_mode, turn_profile, working_source
            )
            ctx = RunContext(
                registries=self._registries,
                agent_id=agent_id,
                user_id=user_id,
                input_content=input_content,
                working_source=working_source,
                pass_mcp_servers=pass_mcp_servers,
                job_instance_id=job_instance_id,
                forced_narrative_id=forced_narrative_id,
                trigger_extra_data=trigger_extra_data or {},
                cancellation=cancellation,
                turn_profile=turn_profile,
                steering=steering,
            )
            ctx.run_id = run_id

            # The seven-stage turn pipeline (narranexus.platform.turn) runs the
            # stages through the strategies the profile names; the legacy
            # fast_mode / silent / TurnProfile flags resolve to a builtin profile.
            # Every stage body is the former inline step block, moved verbatim.
            from narranexus.platform.turn import resolve_profile, turn_pipeline_for
            from narranexus.platform.turn.inputs import TurnServices

            profile = resolve_profile(
                fast_mode=fast_mode,
                silent=silent,
                turn_profile=turn_profile,
                working_source=working_source,
                explicit=pipeline_profile,
                registries=self._registries,
            )
            # The cost scope is entered UNCONDITIONALLY before the pipeline
            # runs: a run with no Event row must set the ambient id to None,
            # not inherit it. A nested run started from a post-turn callback
            # copies the parent's context, so leaving the parent's id in place
            # would book the child's whole spend onto the parent turn — a
            # number that reads high with nothing to show it is wrong. The
            # log binding (bind_event) stays conditional on purpose: inheriting
            # the outer run_id in logs is what you want there. Both are then
            # narrowed to the real event id the moment Ingress creates it, via
            # ``TurnServices.bind_event`` (the pipeline calls it at every stage
            # boundary and yield, so Recall's helper LLMs are already scoped).
            _trace_stack.enter_context(cost_event_scope(None))

            def _bind_turn_event(event_id: str) -> None:
                _trace_stack.enter_context(bind_event(event_id=event_id))
                _trace_stack.enter_context(cost_event_scope(event_id))

            services = TurnServices(
                db_client=db_client,
                event_service=self.event_service,
                session_service=self.session_service,
                narrative_service=self.narrative_service,
                markdown_manager=self.markdown_manager,
                trajectory_recorder=self.trajectory_recorder,
                hook_manager=self.hook_manager,
                response_processor=self._response_processor,
                execute_callback_instance=self._execute_callback_instance,
                timings={"run_start": _t_run_start},
                bind_event=_bind_turn_event,
            )
            # The pipeline CLASS comes from the ``turn.pipeline`` binding, not
            # from an import: the platform must not know which plugin fills the
            # slot whose whole purpose is replacing the turn runtime.
            pipeline = turn_pipeline_for(self._registries)(self._registries)
            async for msg in pipeline.run(ctx, profile, services, silent=silent):
                yield msg

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
            logger.error(f"[Background] No agent_id available for instance: {instance_id}")
            return

        logger.info(f"[Background] Executing callback instance: {instance_id}")

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

            logger.info(f"[Background] Instance {instance_id} execution completed")

        except Exception as e:
            logger.exception(f"[Background] Instance {instance_id} execution failed: {e}")
            # Can record the error to database or send notifications here

    async def _ensure_database_client(self) -> DatabaseClientType:
        """
        Ensure database client is initialized (lazy loading)

        Returns:
            DatabaseClient or AsyncDatabaseClient instance
        """
        if self._database_client is None:
            # Use the globally shared AsyncDatabaseClient (singleton pattern)
            from narranexus.platform.utils.db.db_factory import get_db_client
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

        NOTE: We intentionally do NOT close the database client here.
        The client is a global singleton from get_db_client(), shared across
        all requests and background tasks (hooks). Closing it here would
        break background after_turn tasks that are still
        writing chat history, awareness updates, etc.

        The database connection is managed by the application lifecycle
        (FastAPI lifespan) via close_db_client().
        """
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
            user_id="user_demo",
            input_content="Do you know what a vector bundle is?",
            working_source=WorkingSource.CHAT,  # Use enum type (also supports string "chat")
        ):
            logger.info(f"response: {response}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_agent_runtime())
