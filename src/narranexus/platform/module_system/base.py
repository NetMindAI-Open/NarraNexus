"""
@file_name: base.py
@author: NetMind.AI
@date: 2025-12-22
@description: Module base class definition

Per design document:
- Module provides special capabilities to Agent (e.g., Chat, Task, Social-Network)
- Module contains: Instructions, Tools, Data, Trigger
- Module is a functional department, Narrative is a project team
- Instance belongs to a specific Narrative
"""

import os
from abc import ABC, abstractmethod
from typing import Optional, List, Any, TYPE_CHECKING

from loguru import logger

# Import schema
from narranexus.platform.schema import (
    ModuleConfig,
    MCPServerConfig,
    ModuleInstructions,
    ContextData,
    HookAfterExecutionParams,
)

# Import utils
from narranexus.contracts.agent.capability import STAGE_METHODS, TIER_STAGES, CapabilityMeta, CapabilityTier, ToolSurface
from narranexus.contracts.agent.stages import Stage
from narranexus.platform.utils import DatabaseClient
from narranexus.platform.utils.mcp_executor import list_mcp_tools


def _is_signature_typeerror(exc: TypeError) -> bool:
    """Did the CALL fail, or did the callee's body raise?

    A signature TypeError is raised while binding arguments, so it never enters the
    callee — its traceback has exactly one frame, ours. A TypeError from inside a
    correctly-shaped implementation has at least one more. True only for an
    UNDECORATED callable (a ``functools.wraps`` wrapper absorbs the arity check);
    both arms fail open identically, so only the log text is at stake.
    """
    tb = exc.__traceback__
    return tb is None or tb.tb_next is None

if TYPE_CHECKING:
    from narranexus.platform.utils.db.database import AsyncDatabaseClient


def mcp_host() -> str:
    """
    Resolve the host used to reach MCP servers from this process.

    Defaults to 127.0.0.1 so single-host / systemd / `bash run.sh`
    deployments keep working. In container setups where MCP runs in a
    separate container (e.g. Docker Compose on one EC2, or Phase 2
    Fargate), set MCP_HOST to the container / service name so loopback
    calls resolve across the network instead of hitting the caller's own
    loopback.
    """
    return os.getenv("MCP_HOST", "127.0.0.1")


def mcp_port() -> int:
    """The ONE port the module MCP host listens on (plugin platform batch 5a):
    every module server is mounted by path under it, so no module owns a port."""
    return int(os.getenv("MCP_PORT", "7801"))


def mcp_base_url() -> str:
    """Base URL of the module MCP host as seen from THIS process — ``MCP_BASE_URL``
    when a reverse proxy fronts it, else ``http://<MCP_HOST>:<MCP_PORT>``."""
    return (os.getenv("MCP_BASE_URL") or f"http://{mcp_host()}:{mcp_port()}").rstrip("/")


def mcp_mount_path(server_name: str) -> str:
    """Where a module's MCP server is mounted on the host: ``/mcp/<server_name>``."""
    return f"/mcp/{server_name}"


def mcp_server_url(server_name: str) -> str:
    """The SSE endpoint a module advertises in its ``MCPServerConfig`` — the
    single host + the module's mount path (``…/mcp/<server_name>/sse``). Codex's
    adapter rewrites the trailing ``/sse`` to the streamable ``/mcp`` endpoint."""
    return f"{mcp_base_url()}{mcp_mount_path(server_name)}/sse"


def working_source_matches(working_source: Any, source_name: str) -> bool:
    """True when ``working_source`` names ``source_name``.

    ``WorkingSource`` is a ``(str, Enum)``, so one equality covers both
    the enum member and its serialized string form — a member equals its
    value. The single shared predicate exists so every
    ``claims_source`` override compares the same way (four
    hand-rolled variants with opposite ``isinstance`` polarities is how
    real divergence starts).
    """
    return working_source == source_name


class XYZBaseModule(ABC):
    """
    Base class for all Modules

    Per design document, each Module contains:
    1. **Instructions** - Instructions telling the Agent how to use this capability
    2. **Tools (MCP)** - Tools that the Agent can call
    3. **Data** - Persistently stored data (via Database)
    4. **Trigger** - Ways to activate the Module (optional, some Modules have this)

    Core methods of Module:
    - get_config() - Return Module configuration
    - data_gathering() - Collect data and enrich ContextData
    - contribute_instructions() - Return instructions to add to system prompt
    - mcp_server() - Return MCP Server configuration (if any)
    - create_mcp_server() - Create MCP Server instance (if any)

    Module data isolation:
    - Each Module's data is isolated by agent_id + user_id
    - Data is stored in respective database tables

    MCP database connection:
    - MCP tools call `get_mcp_db_client()` which thin-wraps `get_db_client()`
    - No per-class cache — the factory is already loop-aware and rebuilds
      the aiomysql pool when the current running loop changes.
    """

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str],
        database_client: DatabaseClient,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        """
        Initialize Module

        Args:
            agent_id: Agent ID (for data isolation)
            user_id: User ID (for data isolation, some Modules may not need this)
            database_client: Database client
            instance_id: Instance ID (if provided, indicates operation for a specific instance)
            instance_ids: All instance IDs associated with the Narrative (used for gather, etc.)
        """
        self.agent_id = agent_id
        self.user_id = user_id
        self.db = database_client

        # Instance-related
        self.instance_id = instance_id
        self.instance_ids = instance_ids or []

        self.config = self.get_config()
        self.instructions = ""
        self.state = {}

    # =========================================================================
    # MCP Database Client
    # =========================================================================

    @classmethod
    async def get_mcp_db_client(cls) -> "AsyncDatabaseClient":
        """
        Get the shared async database client for MCP tool handlers.

        Delegates directly to `get_db_client()` without an extra class-level
        cache. The factory already keeps a single AsyncDatabaseClient and is
        event-loop aware — it rebuilds the aiomysql pool when the current
        running loop changes. Adding a class-level `_mcp_db_client` cache on
        top of that used to hide the loop-change signal: once the cached
        reference was set, subsequent tool calls bypassed the factory and
        received a pool bound to a dead or different loop, producing
        "Future attached to a different loop" errors from `Pool._wakeup()`.

        Example:
            @mcp.tool()
            async def my_tool(arg: str) -> str:
                db = await XYZBaseModule.get_mcp_db_client()
                result = await db.get_one("table", {"id": arg})
                return str(result)
        """
        from narranexus.platform.utils.db.db_factory import get_db_client
        return await get_db_client()

    @classmethod
    async def close_mcp_db_client(cls) -> None:
        """
        No-op kept for API compatibility.

        The factory owns the shared client's lifecycle. Shutting MCP down
        should call `close_db_client()` from db_factory instead.
        """
        return None

    # =========================================================================
    # Functional Information
    # =========================================================================

    async def get_module_functional_information(self) -> str:
        """
        Return the functional information of the Module

        Returns:
            str: Functional information of the Module
        """
        mcp_tools = []
        mcp_config = await self.mcp_server()
        if mcp_config and mcp_config.server_url != "":
            mcp_server_url = mcp_config.server_url
            mcp_tools = await list_mcp_tools(mcp_server_url)

        functional_information = f"""
Module: {self.config.name}
Instructions: {self.instructions}
MCPs: {mcp_tools}
------------------------------------------------------------
        """
        return functional_information

    # =========================================================================
    # Instructions
    # =========================================================================

    async def contribute_instructions(self, ctx_data: ContextData) -> str:
        """
        Return instructions to add to the system prompt

        Per design document:
        - Module Instructions contain System Prompts and placeholders
        - They are sorted by priority and concatenated into the system prompt

        Module can dynamically generate instructions based on ctx_data (e.g., adjust based on chat history length)

        Args:
            ctx_data: Context data (Module may need to dynamically generate instructions based on data)

        Returns:
            Instruction string to include in system prompt
        """
        local_ctx_data = ctx_data.model_copy()
        local_ctx_data = local_ctx_data.model_dump()
        instruction = self.instructions.format(**local_ctx_data)
        return instruction

    async def contribute_turn_context(self, ctx_data: ContextData) -> str:
        """Per-turn volatile context for the CURRENT user message.

        Content that changes every turn (retrieved data, live counters,
        timestamps, dynamic lists) belongs here, NOT in contribute_instructions —
        contribute_instructions must stay byte-stable across turns so the system
        prompt stays cacheable (provider prefix caches are byte/blockwise
        and any per-turn byte breaks them). The runtime collects these
        blocks (deduplicated by module class, priority ascending) into a
        "[Turn context]" block prepended to the current user message; a
        module failure here is logged and skipped, never fatal to the turn.
        Default: no per-turn context.

        Args:
            ctx_data: Context data for the current turn

        Returns:
            Markdown block to include in the current turn's context,
            or "" when the module has nothing volatile to contribute.
        """
        return ""

    @classmethod
    async def on_instance_activated(cls, instance_id: str, database_client: Any) -> None:
        """A blocked instance of this module became ACTIVE (its dependencies
        completed). Default no-op; a task module reschedules its work here
        (JobModule sets the job's next run time). Called by the narrative
        instance handler by module class — the platform names no module."""
        return None

    @staticmethod
    @abstractmethod
    def get_config() -> ModuleConfig:
        """
        Return Module configuration — static, so the platform can read a module's
        declaration from its CLASS (registry views, display, decision prompt)
        without constructing it.

        Returns:
            ModuleConfig
        """
        ...

    # =========================================================================
    # Capability flags
    # =========================================================================
    #
    # Capability flags let the orchestration layer reason about WHAT a module
    # does without hard-coding WHICH concrete class does it (e.g. avoid
    # `type(m).__name__ == "ChatModule"`). A capability flag is a classmethod
    # so it can be queried from both a live module object and a class-name
    # string (via module_registry) — see module.module_class_provides_chat_history.

    @classmethod
    def provides_chat_history(cls) -> bool:
        """Whether this module is the per-user carrier of chat history /
        conversation persistence.

        The pipeline uses this to find the chat-bearing instance, re-bind
        chat persistence on narrative routing, and skip other users'
        chat instances during hook loading — all without naming ChatModule
        directly. Default False; the chat module overrides to True.
        """
        return False

    # =========================================================================
    # Hooks
    # =========================================================================

    async def gather(self, ctx_data: ContextData) -> ContextData:
        """
        Collect data and enrich ContextData

        This is one of the core methods of Module. The Module will:
        1. Read relevant data from Database
        2. Add data to ctx_data

        For example:
        - ChatModule reads chat history from database and adds it to ctx_data.chat_history
        - SocialNetworkModule reads user profiles and adds them to ctx_data.user_profile

        Args:
            ctx_data: Context data (will be modified)

        Returns:
            Enriched ContextData
        """
        return ctx_data

    async def persist_turn(self, params: HookAfterExecutionParams) -> None:
        """
        Synchronous, next-turn-critical persistence — runs INSIDE the request,
        before the WebSocket closes and before `after_turn` is
        dispatched to the background.

        Use this ONLY for state that the IMMEDIATELY NEXT turn must be able to
        read. The canonical case: ChatModule writes the conversation row here, so
        a user who replies the instant they see the answer cannot race the write
        (the background-hook path could lag seconds-to-tens-of-seconds, which
        manifested as the agent "forgetting" the turn it just had).

        Keep this CHEAP — it adds latency to every turn's completion. Anything
        heavy and non-next-turn-critical (entity extraction, LLM
        summaries, job analysis) belongs in `after_turn`.

        Default: no-op. Most modules don't need synchronous persistence.

        Args:
            params: HookAfterExecutionParams (same payload as the background hook).
        """
        return None

    async def after_turn(self, params: HookAfterExecutionParams) -> None:
        """
        Background enrichment — runs AFTER the WebSocket closes, dispatched as a
        fire-and-forget task. The user has already seen the response; nothing the
        next turn strictly needs may live only here (see `persist_turn`).

        Use for heavy, non-next-turn-critical work: entity
        extraction, memory summarization, external-system updates, job-completion
        callbacks.

        Args:
            params: HookAfterExecutionParams, containing:
                - execution_ctx: Execution context (event_id, agent_id, user_id, working_source)
                - io_data: Input/output (input_content, final_output)
                - trace: Execution trace (event_log, agent_loop_response)
                - ctx_data: Complete context data
        """
        return None

    # =========================================================================
    # Capability contract (plugin platform batch 5c): a module IS a Capability
    # =========================================================================

    @property
    def meta(self) -> CapabilityMeta:
        """``CapabilityMeta`` derived from the module's own ``ModuleConfig``.

        Static description only. ``ModuleConfig.enabled`` is per-agent state
        and stays on ``self.config`` (the value ``CapabilitySet.is_enabled``
        takes as its default); it is deliberately NOT folded into
        ``requires``, which names runtime requirements, so the runtime has one
        source of truth for "is this capability on for this agent".
        """
        config = self.config
        return CapabilityMeta(
            name=config.name,
            tier=CapabilityTier.MODULE,
            display_name=(config.display.name if config.display and config.display.name else config.name),
            description=config.description,
            priority=config.priority,
            always_load=config.always_load or config.module_type == "capability",
            is_task_capability=config.module_type == "task",
            provides_chat_history=type(self).provides_chat_history(),
            context_cost_hint=config.context_cost_hint,
            instance_prefix=config.effective_instance_prefix(),
        )

    def participations(self) -> "dict[Stage, Any]":
        """The stages this module fills — every participant stage of the MODULE tier,
        each answered by the module itself: its lifecycle methods ARE the stage
        participations (``claims_source`` / ``gather`` / ``contribute_instructions`` /
        ``contribute_turn_context`` / ``contribute_tools`` / ``persist_turn`` /
        ``after_turn``). ``recall`` and ``tools`` are not module cells."""
        return {stage: self for stage in TIER_STAGES[CapabilityTier.MODULE] if any(hasattr(self, m) for m in STAGE_METHODS[stage])}

    async def contribute_tools(self, ctx_data: Any = None) -> ToolSurface:
        """What this module adds to (and removes from) the turn's tool surface —
        the Assemble-stage cell. Composed from the three finer hooks a module
        overrides (``mcp_server`` / ``expressive_tools`` / ``disallowed_tools``);
        each part fails OPEN on its own (a crashing declaration contributes
        nothing), and a stale override SIGNATURE is logged loudly — that exact
        drift once silently muted a module's reply surface for a whole turn.
        """
        name = getattr(getattr(self, "config", None), "name", None) or type(self).__name__
        servers: dict[str, Any] = {}
        try:
            cfg = await self.mcp_server()
            if cfg is not None:
                servers[cfg.server_name] = cfg.model_dump() if hasattr(cfg, "model_dump") else dict(vars(cfg))
        except Exception as e:  # noqa: BLE001 — fail-open
            logger.warning(f"mcp_server failed for {name}: {e}")
        suppressed: list[str] = []
        try:
            suppressed = list(await self.disallowed_tools(ctx_data) or [])
        except TypeError as e:
            if _is_signature_typeerror(e):
                logger.error(f"disallowed_tools signature mismatch for {name} (suppression DROPPED): {e}")
            else:
                logger.exception(f"disallowed_tools raised for {name} (suppression DROPPED)")
        except Exception as e:  # noqa: BLE001 — fail-open
            logger.warning(f"disallowed_tools failed for {name}: {e}")
        declared: list[str] = []
        try:
            declared = list(await self.expressive_tools(ctx_data) or [])
        except TypeError as e:
            if _is_signature_typeerror(e):
                logger.error(f"expressive_tools signature mismatch for {name} (declaration DROPPED): {e}")
            else:
                logger.exception(f"expressive_tools raised for {name} (declaration DROPPED)")
        except Exception as e:  # noqa: BLE001 — fail-open
            logger.warning(f"expressive_tools failed for {name}: {e}")
        return ToolSurface(mcp_servers=servers, expressive_tools=tuple(declared), disallowed_tools=tuple(suppressed))

    # =========================================================================
    # MCP Server
    # =========================================================================

    @abstractmethod
    async def mcp_server(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        If this Module needs to provide an MCP Server (i.e., provide Tools), return the configuration
        If not needed, return None

        Per design document:
        - Module Tools use Description to let Agent understand how to use them
        - Tools can be provided by MCP Server

        Returns:
            MCPServerConfig or None
        """
        pass

    async def expressive_tools(self, ctx_data: Any = None) -> list[str]:
        """Fully-qualified reply/delivery tools this module contributes.

        The platform forwards the collected list to the agent framework
        as the turn's expressive surface (NexusPower's monologue
        contract: only these tools' content reaches a human). Most
        modules deliver nothing themselves — default is empty; chat and
        IM channel modules override.

        ``ctx_data`` (optional, the turn's ContextData) lets a module vary
        its declaration by turn origin — the surface must never name a
        tool that cannot deliver on THIS turn (e.g. narramessenger's
        trigger-captured ``narra_reply`` on a platform-forwarded managed
        turn). Declaring a dead tool is misinformation to the model.
        """
        return []

    def claims_source(self, working_source: Any) -> bool:
        """True when THIS module is the origin of the given working_source.

        The expressive collection sorts the origin module's declaration
        first, so "whoever contacted you" decides the turn's default
        reply tool — priority order alone would hand every turn to the
        owner-chat tool regardless of where the contact came from.
        Modules that never originate turns keep the False default;
        overriders compare via ``working_source_matches``.
        """
        return False

    async def disallowed_tools(self, ctx_data: Any = None) -> list[str]:
        """
        Fully-qualified MCP tool names to suppress for THIS agent THIS turn.

        Takes ``ctx_data`` for the same reason `expressive_tools` does:
        a module that suppresses the counterpart of the verb it declares has
        to read the SAME turn both hooks are deciding about. It was briefly
        ctx-less, with the turn remembered on the instance by the
        declaration — but the runtime calls suppression FIRST, so on a fresh
        instance that state was always empty and every team turn declared
        `message_team` while suppressing it. Reading the argument removes
        the ordering dependency rather than documenting it.

        The module's MCP server is a shared process serving every agent, so
        per-agent tool trimming cannot happen server-side. Names returned
        here (``mcp__<server_name>__<tool_name>``) are merged into the CLI's
        ``disallowed_tools``, which removes the tool schemas from the model
        context entirely (verified 2026-07-24: disallowing 11 built-in tools
        shrank the cached prefix by ~4.1K tokens).

        Default: nothing suppressed. ChannelModuleBase overrides this to
        suppress non-setup tools while the channel is unbound.
        """
        return []

    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server instance

        If this Module needs to provide an MCP Server, implement this method
        The returned Server instance will be used by ModuleRunner for deployment

        Per design document:
        - MCP Server logic can be written in a single class (recommended for simple MCP)
        - Can also bridge to a separate file (recommended for complex MCP)

        Returns:
            MCP Server instance or None
        """
        return None

    def build_instrumented_mcp_server(self) -> Optional[Any]:
        """Deployment-facing wrapper around :meth:`create_mcp_server`.

        Subclasses keep overriding ``create_mcp_server`` (the documented
        extension point); serving code calls THIS so every module — present
        and future — gets the platform-level wiring with no per-module step
        to forget. Currently that wiring is caller-identity resolution: the
        module MCP servers are one shared process per module, so a tool's
        ``agent_id`` parameter used to be whatever the MODEL typed, and a
        model that guessed ``"agent_current"`` hit a hard dead end and told
        the user the task was impossible (P1, evt_0dcee899). See
        ``module/_mcp_identity.py``.

        Never raises: a module whose server cannot be instrumented is still
        served uninstrumented (identity resolution is an improvement, not a
        precondition).
        """
        mcp_server = self.create_mcp_server()
        if mcp_server is None:
            return None
        try:
            from narranexus.platform.module_system._mcp_identity import (
                install_caller_identity,
            )

            install_caller_identity(mcp_server)
        except Exception as e:  # noqa: BLE001 — never block serving
            logger.warning(
                f"[{self.__class__.__name__}] caller-identity resolution "
                f"not installed: {e}"
            )
        return mcp_server

    # =========================================================================
    # Database
    # =========================================================================

    async def init_database_tables(self) -> None:
        """
        Initialize database tables needed by the Module

        Per design document:
        - Each Module has its own database tables
        - Data is isolated by agent_id + user_id

        Module can override this method to create its own required tables
        """
        pass

    def get_table_schemas(self) -> List[str]:
        """
        Return database table definitions needed by the Module

        Returns a list of SQL CREATE TABLE statements

        Returns:
            List of SQL statements
        """
        return []

    # =========================================================================
    # Instance Parts
    # =========================================================================

    def get_instance_object_candidates(self, **kwargs) -> List[Any]:
        """
        Return the list of instance object candidates for the Module

        Returns:
            List of instance objects
        """
        return []

    def create_instance_object(self, **kwargs) -> Any:
        """
        Create a Module instance object

        Args:
            **kwargs: Creation parameters

        Returns:
            Instance object
        """
        return None

    def update_instance_object(self, **kwargs) -> None:
        """
        Update a Module instance object

        Args:
            **kwargs: Update parameters
        """
        return None

    def delete_instance_object(self, **kwargs) -> None:
        """
        Delete a Module instance object

        Args:
            **kwargs: Deletion parameters
        """
        return None
