"""
Module Runner - Deploy A2A API Server and MCP Servers

@file_name: module_runner.py
@author: NetMind.AI
@date: 2025-11-07
@description: Unified service deployment for MCP servers and A2A API

=============================================================================
Supported Running Modes
=============================================================================

1. run_mcp_servers_async(agent_id, user_id, modules)
   Run every module's MCP server in ONE process on ONE port (MCP_PORT),
   each mounted at /mcp/<server_name> (plugin platform batch 5a)

2. run_api_server(host, port)
   Run A2A Protocol API Server

3. run_module(agent_id, modules, api_host, api_port)  [Recommended]
   Run A2A API Server and the MCP host together

=============================================================================
Usage Examples
=============================================================================

CLI:
    python -m narranexus.platform.module_system.module_runner module  # Full deployment
    python -m narranexus.platform.module_system.module_runner api     # API only
    python -m narranexus.platform.module_system.module_runner mcp     # MCP only

Python:
    from narranexus.platform.module_system.module_runner import ModuleRunner

    runner = ModuleRunner()
    runner.run_module()  # Full deployment

    # Or run specific modules
    asyncio.run(runner.run_mcp_servers_async(
        agent_id="my_agent",
        user_id="my_user",
        modules=["AwarenessModule", "JobModule"]
    ))

=============================================================================
Architecture
=============================================================================

                    ┌─────────────────────────────────┐
                    │         ModuleRunner            │
                    └─────────────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
          ▼                        ▼                        ▼
    ┌───────────┐          ┌───────────────────────┐   ┌───────────────────────┐
    │ A2A API   │          │ MCP host :7801        │   │ /mcp/awareness_module │
    │ Server    │          │ (one port, one loop)  │──▶│ /mcp/job_module  …    │
    │ :8000     │          │                       │   │ (mounted by path)     │
    └───────────┘          └───────────────────────┘   └───────────────────────┘
          │                        │                        │
          │                        └────────────┬───────────┘
          ▼                                     ▼
    ┌─────────────┐                    ┌─────────────┐
    │   External  │                    │   Agent     │
    │   Clients   │                    │   Runtime   │
    │   (A2A)     │                    │   (Tools)   │
    └─────────────┘                    └─────────────┘
"""

import asyncio
import contextlib
import multiprocessing
import signal
from typing import Any, List, Optional, Type, Union

from loguru import logger

# Module (same package)
from narranexus.platform.module_system import XYZBaseModule, module_registry
from narranexus.platform.module_system.base import mcp_mount_path, mcp_port

#: Tightest SIGTERM->SIGKILL window any supervisor gives this process: the
#: Tauri sidecar manager waits 3s (``process_manager.rs::stop_service``);
#: Docker's default ``stop_grace_period`` (the deploy compose sets none for
#: ``mcp``) is 10s. Everything in ``_release_host_resources`` must fit inside it.
_STOP_GRACE_BUDGET_SEC = 3.0
#: Time reserved after the drain for cancel+gather, closing the pool and
#: flushing loguru. A drain that eats the whole grace gets the process
#: SIGKILLed before the pool closes -- exactly when there was in-flight work.
_POOL_CLOSE_HEADROOM_SEC = 1.0
#: How long shutdown waits for detached (`spawn`ed) work before cancelling it
#: and closing the pool. Derived, never set independently: it MUST stay below
#: the stop grace or the close step it protects never runs. The work is
#: cancelled, never silently dropped.
_BACKGROUND_DRAIN_SEC = _STOP_GRACE_BUDGET_SEC - _POOL_CLOSE_HEADROOM_SEC

# Utils
from narranexus.platform.utils import (
    DatabaseClient,
    close_db_client,
    drain_background_tasks,
    get_db_client,
    get_db_client_sync,
    pending_background_tasks,
)


@contextlib.contextmanager
def _no_signal_capture():
    """Drop-in no-op replacement for ``uvicorn.Server.capture_signals``.

    Every ``uvicorn.Server.serve()`` wraps itself in ``capture_signals()``,
    which calls ``signal.signal(SIGTERM/SIGINT, self.handle_exit)`` — a
    PROCESS-GLOBAL registration. The MCP host neutralises uvicorn's capture
    and installs ONE handler in ``run_mcp_servers_async`` (SIGINT and SIGTERM)
    that stops the host, so shutdown is owned in one place — mirrors
    ``run_worker_supervisor``'s central signal handling — and a delivered
    SIGTERM always releases the port (the historical bug: N per-module
    servers each captured the signal, the last one won, and the rest kept
    their ports until an external SIGKILL).
    """
    yield


# =============================================================================
# Default Configuration
# =============================================================================

# No module owns a port (plugin platform batch 5a): the host serves every
# module server on ONE port (``module/base.py`` ``mcp_port()``), each mounted at
# ``/mcp/<server_name>``. Core vs channel modules are told apart by the
# contribution meta the plugin declared (``channel``), read from the registry
# at call time — the platform holds no module table.


def discover_channel_modules(module_map: dict) -> list[str]:
    """Names of every ChannelModuleBase subclass in ``module_map`` (sorted)."""
    # Lazy import to avoid circular dep with channel/ → module/
    from narranexus.platform.channel.channel_module_base import ChannelModuleBase

    return sorted(
        name for name, cls in module_map.items()
        if isinstance(cls, type) and issubclass(cls, ChannelModuleBase)
    )


def all_mcp_modules() -> list[str]:
    """Every module the registry knows at CALL time — builtin core, channel and
    plugin-contributed alike (a plugin module that ships an MCP server is
    served like any other; the runner skips a module whose ``mcp_server()``
    answers None). Derived from ``module_registry``, never from the platform's
    builtin table, and never at import: user plugins register at boot."""
    from narranexus.platform.module_system import module_registry

    core = [name for name in module_registry if name not in discover_channel_modules(module_registry)]
    return sorted(core) + discover_channel_modules(module_registry)





def _a2a_server_class() -> type:
    """The A2A protocol server class: builtin.chat's ``ingress.triggers`` entry ``a2a`` (host="api").

    Resolved through the registry so the runner never imports ChatModule; with
    builtin.chat disabled there is no A2A ingress and this fails loud.
    """
    from narranexus.contracts._base import UnknownEntry
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    registry = KERNEL_REGISTRIES.registry_for("ingress.triggers")
    try:
        spec = registry.get("a2a")
    except UnknownEntry:
        raise RuntimeError("A2A server unavailable: no 'a2a' ingress trigger is registered (builtin.chat disabled?)") from None
    if spec.host != "api":
        raise RuntimeError(f"A2A trigger must be host='api', got {spec.host!r}")
    return spec.resolve()


class ModuleRunner:
    """
    Module Runner - Deploy and manage MCP Servers and A2A API.

    Features:
    - One MCP host: every module server on one port, mounted by path
    - Automatic module discovery from module_registry
    - Flexible configuration (module names or classes)

    Usage:
        runner = ModuleRunner()

        # Run all default MCP servers
        asyncio.run(runner.run_mcp_servers_async())

        # Run with full deployment (A2A + MCP)
        runner.run_module()
    """

    def __init__(self):
        # Do not eagerly call get_db_client_sync() here: it runs
        # asyncio.run(AsyncDatabaseClient.create()), which tears down the
        # temporary loop and leaves the aiomysql pool bound to a dead loop.
        # Any later async call from a different event loop (MCP's anyio
        # TaskGroup in particular) blows up with "Future attached to a
        # different loop". MCP tools use XYZBaseModule.get_mcp_db_client(),
        # which lazy-creates the pool inside the MCP server's own loop.
        pass

    # =========================================================================
    # Module Resolution
    # =========================================================================

    def _resolve_modules(
        self, modules: Optional[Union[List[str], List[Type[XYZBaseModule]]]] = None
    ) -> List[Type[XYZBaseModule]]:
        """
        Resolve module specifications to module classes.

        Accepts either module class names (strings) or module classes directly.

        Args:
            modules: List of module names or classes, or None for defaults

        Returns:
            List of module classes

        Example:
            # By name
            classes = runner._resolve_modules(["AwarenessModule", "JobModule"])

            # By class
            classes = runner._resolve_modules([AwarenessModule, JobModule])

            # Default
            classes = runner._resolve_modules(None)  # Uses all_mcp_modules()
        """
        if modules is None:
            modules = all_mcp_modules()

        resolved = []
        for module in modules:
            if isinstance(module, str):
                # Resolve by name from module_registry
                if module not in module_registry:
                    logger.warning(f"Module '{module}' not found in module_registry, skipping")
                    continue
                resolved.append(module_registry[module])
            elif isinstance(module, type) and issubclass(module, XYZBaseModule):
                resolved.append(module)
            else:
                logger.warning(f"Invalid module specification: {module}, skipping")

        return resolved

    def _create_module_instance(
        self,
        module_class: Type[XYZBaseModule],
        agent_id: str,
        user_id: Optional[str] = None,
        db_client: Optional[DatabaseClient] = None,
    ) -> XYZBaseModule:
        """
        Create an instance of a module.

        Args:
            module_class: Module class to instantiate
            agent_id: Agent ID
            user_id: User ID (optional, defaults to agent_id)
            db_client: Database client (optional, creates new if not provided)

        Returns:
            Module instance
        """
        db = db_client or get_db_client_sync()
        user = user_id or agent_id
        return module_class(agent_id=agent_id, user_id=user, database_client=db)

    # =========================================================================
    # Multiple MCP Servers (Asyncio - Single Process)
    # =========================================================================

    async def run_mcp_servers_async(
        self,
        agent_id: str = "mcp_deploy",
        user_id: Optional[str] = None,
        modules: Optional[Union[List[str], List[Type[XYZBaseModule]]]] = None,
    ) -> None:
        """
        Run multiple MCP servers concurrently in a single process using asyncio.

        All MCP servers share a single event loop inside the same process.
        This is intentional: aiomysql.Pool binds its internal Futures to
        the loop that created the pool, so mixing loops (threads, nested
        anyio.run, multiprocessing) causes "Future attached to a
        different loop" errors. See PLAN-2026-04-22-mcp-single-loop.md.

        Args:
            agent_id: Agent ID for data isolation
            user_id: User ID (defaults to agent_id)
            modules: List of module names or classes

        Example:
            runner = ModuleRunner()
            asyncio.run(runner.run_mcp_servers_async(
                agent_id="my_agent",
                modules=["AwarenessModule", "JobModule"]
            ))
        """
        # Plugin platform boot for the mcp role FIRST: the module table is
        # derived from the registry the boot fills (builtin and user plugin
        # modules alike), so resolving modules before booting resolves nothing.
        # Registers declarative contributions (tools / mcp servers / skills);
        # user plugin code is not activated in this process.
        from narranexus.platform.module_system.plugins_boot import boot_mcp_plugins

        boot_mcp_plugins()

        module_classes = self._resolve_modules(modules)

        if not module_classes:
            logger.error("No modules to run")
            return

        user = user_id or agent_id

        # Everything past this point may have opened the pool; ONE finally
        # releases it (see _release_host_resources) whichever way we leave —
        # serve() returning, the "no MCP servers" early return, auto_migrate
        # raising. The first version closed the pool only after serve(), so
        # the two start-up failure exits still leaked it.
        # The pool is opened OUTSIDE the try (a failed open has nothing to
        # release) and migrated INSIDE it (a failed migration must still
        # close what was just opened — the test that caught this had
        # migrate raise inside the opener, leaving `db` unbound).
        db = await self._open_pool_unless_seamed()
        try:
            if db is not None:
                from narranexus.platform.utils.db.schema_registry import auto_migrate

                # MCP runs as a separate process from the backend here, so it
                # ensures the tables exist itself.
                await auto_migrate(db._backend)
                logger.info("Schema auto-migration complete")
            await self._serve_host(agent_id, user, module_classes, db)
        finally:
            await self._release_host_resources(db)

    @staticmethod
    async def _open_pool_unless_seamed() -> Optional[DatabaseClient]:
        """The pool this process owns (built on THIS loop), or None in seam/HttpStore mode."""
        if _seam_uses_backend():
            # Seam is HttpStore: every DB-touching tool forwards to the backend,
            # so this process needs no pool and must NOT run migrations (backend
            # owns them; the mcp container may hold no DB creds / DDL grant).
            # Modules get database_client=None, exactly like the multi-process
            # path (_run_single_mcp) — that is what makes single-process cloud mcp
            # genuinely creds-free.
            logger.info("MCP async mode: seam=HttpStore → no DB pool, skipping auto_migrate")
            return None
        # SQLite / local dev: this runner DOES hold the pool. Build it on THIS
        # loop (aiomysql binds Futures to the creating loop).
        return await get_db_client()

    async def _serve_host(
        self,
        agent_id: str,
        user: str,
        module_classes: List[Type[XYZBaseModule]],
        db: Optional[DatabaseClient],
    ) -> None:
        """Build every module's server and serve them on ONE uvicorn host until stopped."""
        logger.info("Starting MCP Servers (async mode)")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   User ID: {user}")
        # Create module instances; each server is keyed by the server_name its
        # module advertises (the mount path the agent side dials).
        instances: list[tuple[str, Any]] = []
        for module_class in module_classes:
            module = module_class(agent_id=agent_id, user_id=user, database_client=db)
            mcp_server = module.build_instrumented_mcp_server()  # _mcp_identity.py
            config = await module.mcp_server()
            if mcp_server and config is not None:
                instances.append((config.server_name, mcp_server))
                logger.info(f"{module_class.__name__} ready → {mcp_mount_path(config.server_name)}")
            else:
                logger.info(f"{module_class.__name__} has no MCP server")

        if not instances:
            logger.error("No MCP servers to run")
            return

        logger.info(f"\n✅ {len(instances)} MCP servers ready to start")

        # Every module server is served by ONE uvicorn server on THIS loop: one
        # host app mounts each module's dual-transport app by path. One loop
        # keeps aiomysql.Pool's Futures bound to the loop that processes
        # requests (see PLAN-2026-04-22-mcp-single-loop.md); one port means the
        # agent side, the desktop preflight and compose know a single address.
        port = mcp_port()
        server = self._build_host_server(instances, port)
        for server_name, _ in instances:
            logger.info(f"{server_name} → http://0.0.0.0:{port}{mcp_mount_path(server_name)}/sse")

        # Centralised graceful shutdown: SIGINT/SIGTERM flip should_exit so
        # serve() returns, asyncio.run() unwinds and the port is released.
        # Mirrors run_worker_supervisor's SIGINT+SIGTERM handling (iron rule
        # #7: run.sh and the desktop app must behave the same on shutdown).
        loop = asyncio.get_running_loop()

        def _request_shutdown() -> None:
            logger.info("Signal received — stopping the MCP host")
            server.should_exit = True

        installed_signals = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_shutdown)
                installed_signals.append(sig)
            except NotImplementedError:  # pragma: no cover — non-Unix
                pass

        logger.info(f"\n✅ MCP host running on port {port} ({len(instances)} module servers, single-process, single-loop)")
        # Serving: the mcp boot may now clear its marker and move the LKG.
        from narranexus.platform.module_system.plugins_boot import mark_host_healthy

        mark_host_healthy("mcp")

        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("MCP host cancelled")
        finally:
            for sig in installed_signals:
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, ValueError):  # pragma: no cover
                    pass

    @staticmethod
    async def _release_host_resources(db: Optional[DatabaseClient]) -> None:
        """Shutdown sequence, in the order the two sibling entrypoints use.

        1. Join detached work FIRST. ``utils.background_tasks.spawn`` hands
           out fire-and-forget tasks from inside tool calls (a hook can start
           a whole run, the dataloader flushes batches, the narrative updater
           writes) — anything still in flight when the pool closes hits
           ``_backend=None``. Bounded (``_BACKGROUND_DRAIN_SEC``), then
           cancelled and gathered: the same stop → cancel → gather shape as
           ``run_worker_supervisor._drain_and_close`` — a wedged task must
           not turn shutdown into a hang, and the process is exiting anyway.
        2. Close the pool this process opened, on THIS loop — aiomysql binds
           its Futures to the creating loop, so it must also be the one to
           close it. Skipped in seam/HttpStore mode (``db is None``): nothing
           was opened. Without this, ``asyncio.run()`` tore the loop down
           with the pool open and aiomysql's connections were finalized by GC
           afterwards, logging "Event loop is closed" once per connection
           (dev logs, 11x).
        3. Flush loguru's async sinks inside this loop scope, as both
           siblings do — a sink flushed after the loop closes is a sink that
           drops the last lines of the shutdown.
        """
        in_flight = pending_background_tasks()
        if in_flight:
            logger.info(f"[mcp] waiting for {len(in_flight)} background task(s) before closing the pool")
            await drain_background_tasks(timeout=_BACKGROUND_DRAIN_SEC)
            leftover = list(pending_background_tasks())
            for task in leftover:
                task.cancel()
            if leftover:
                await asyncio.gather(*leftover, return_exceptions=True)
        if db is not None:
            try:
                await close_db_client()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[mcp] close_db_client failed: {e}")
        flush = logger.complete()
        if hasattr(flush, "__await__"):
            await flush

    @staticmethod
    def _build_module_app(mcp_server: Any) -> Any:
        """One module's Starlette app exposing BOTH MCP transports at its root.

        Why both:
          - Claude Code's MCP client expects ``{type: "sse", url: ".../sse"}``
            (see adapters/claude/sdk.py). It will not work with the
            streamable HTTP transport.
          - OpenAI Codex CLI's MCP client only speaks streamable HTTP
            (POST to a single ``/mcp`` endpoint, optional GET for SSE
            upgrade). It silently fails to connect to a pure SSE endpoint.

        FastMCP's two ``*_app()`` methods return fresh Starlette apps with
        non-overlapping route paths: ``sse_app()`` serves ``/sse`` +
        ``/messages``; ``streamable_http_app()`` serves ``/mcp``. Their routes
        are flattened into ONE app so that, mounted at ``/mcp/<server_name>``
        by the host, the module answers at

            /mcp/<server_name>/sse         ← Claude Code
            /mcp/<server_name>/messages    ← Claude Code (client → server)
            /mcp/<server_name>/mcp         ← Codex CLI

        The SSE transport is mount-aware (it prefixes the messages endpoint it
        advertises with the request's ``root_path``), so mounting is what makes
        one port serve every module. The streamable app's lifespan owns the
        ``StreamableHTTPSessionManager``; it is kept as this app's lifespan and
        entered by the host (Starlette does not run a mounted app's lifespan).
        """
        from starlette.applications import Starlette

        from mcp.server.transport_security import TransportSecuritySettings

        mcp_server.settings.host = "0.0.0.0"
        # FastMCP auto-enables DNS rebinding protection when host is
        # 127.0.0.1 at init time; flipping host afterward does not clear
        # it. Set the policy explicitly so other containers can reach the
        # host by Docker service name (e.g. "mcp:7801").
        mcp_server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
        sse_app = mcp_server.sse_app()
        streamable_app = mcp_server.streamable_http_app()
        return Starlette(
            routes=list(sse_app.router.routes) + list(streamable_app.router.routes),
            lifespan=streamable_app.router.lifespan_context,
        )

    @staticmethod
    def _build_host_server(servers: list[tuple[str, Any]], port: int) -> Any:
        """Build (but do not start) the ONE uvicorn.Server hosting every module.

        ``servers`` is ``[(server_name, FastMCP), …]``; each is mounted at
        ``mcp_mount_path(server_name)``. ``GET /mcp/healthz`` lists what is
        mounted (the desktop app and compose probe it). The caller-identity
        middleware (identity/mcp_auth.py) wraps the host once — ONE choke point
        for every module and both transports; NX_MCP_AUTH_MODE=off (the
        default) keeps it a strict no-op. Each mounted app's lifespan is
        entered by the host's lifespan so the streamable session managers
        start and stop with the host.

        Signal capture is neutralised (``_no_signal_capture``);
        ``run_mcp_servers_async`` owns shutdown centrally.
        """
        import contextlib as _contextlib

        import uvicorn
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        from narranexus.platform.module_system.identity.mcp_auth import IdentityAuthMiddleware

        apps = [(name, ModuleRunner._build_module_app(mcp_server)) for name, mcp_server in servers]
        mounted = [name for name, _ in apps]

        async def _healthz(_request):
            return JSONResponse({"status": "ok", "port": port, "servers": mounted})

        @_contextlib.asynccontextmanager
        async def _lifespan(_app):
            async with _contextlib.AsyncExitStack() as stack:
                for _name, app in apps:
                    await stack.enter_async_context(app.router.lifespan_context(app))
                if browser_service is not None:
                    stack.push_async_callback(browser_service.close)
                yield

        # Browser frames are served from HERE, not from the backend: the
        # session object lives in this process. Approvals cross to the backend
        # through a table, but a frame stream is live bytes — the owner has to
        # serve it and the backend proxies.
        browser_routes = []
        browser_service = None
        if "browser_module" in mounted:
            from narranexus.platform.browser.browser_service import get_shared_service
            from narranexus.platform.browser.stream_bridge import stream_routes

            browser_service = get_shared_service()
            browser_routes = stream_routes(
                browser_service.session_for, on_login_control=browser_service.login_control_changed,
            )

        host_app = Starlette(
            routes=[Route("/mcp/healthz", _healthz)] + browser_routes + [Mount(mcp_mount_path(name), app=app) for name, app in apps],
            lifespan=_lifespan,
            middleware=[Middleware(IdentityAuthMiddleware)],
        )
        config = uvicorn.Config(
            host_app,
            host="0.0.0.0",
            port=port,
            log_level="warning",  # keep CLI quiet; FastMCP logs at debug
            access_log=False,
            # None = skip uvicorn's dictConfig. The default config detaches
            # uvicorn.* loggers from the root logger (propagate=False + own
            # stderr handlers), silently pulling them out of our loguru
            # InterceptHandler bridge — uvicorn noise would bypass the one
            # log format/file the operators watch. log_level still applies.
            log_config=None,
        )
        server = uvicorn.Server(config)
        server.capture_signals = _no_signal_capture
        return server

    # ============================================================================= A2A API Server
    @staticmethod
    def _run_api_server(host: str, port: int):
        """
        Run A2A API server in an independent process

        Args:
            host: Host address
            port: Port number
        """
        server = _a2a_server_class()(host=host, port=port)
        server.run()

    def run_api_server(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        agent_name: str = "XYZ Agent",
        agent_description: str = "XYZ Agent Context - Intelligent Conversational Agent",
    ) -> None:
        """
        Run A2A Protocol API Server

        Start an HTTP server compliant with Google A2A specification, supporting:
        - Agent Card service discovery (GET /.well-known/agent.json)
        - JSON-RPC 2.0 endpoint (POST /)
        - SSE streaming responses

        Args:
            host: Host address, default "0.0.0.0"
            port: Port number, default 8000
            agent_name: Agent name
            agent_description: Agent description
        """
        A2AServer = _a2a_server_class()

        logger.info("Starting A2A Protocol API Server...")
        logger.info(f"   Agent: {agent_name}")
        logger.info(f"   Host: {host}")
        logger.info(f"   Port: {port}")
        logger.info("   Protocol: A2A/0.3 (Google Agent-to-Agent)")
        server = A2AServer(host=host, port=port, agent_name=agent_name, agent_description=agent_description)
        server.run()

    # =========================================================================
    # Run Module (A2A API + MCP) - Full Deployment
    # =========================================================================

    def run_module(
        self,
        agent_id: str = "module_deploy",
        user_id: Optional[str] = None,
        modules: Optional[Union[List[str], List[Type[XYZBaseModule]]]] = None,
        api_host: str = "0.0.0.0",
        api_port: int = 8000,
    ) -> None:
        """
        Run A2A API Server and all MCP Servers together [Recommended].

        This is the main entry point for full deployment:
        1. A2A Protocol API Server (Google A2A compliant)
        2. All specified MCP Servers (tools for Agent)

        Deployed services:
        - A2A API: http://{api_host}:{api_port}
          - GET  /.well-known/agent.json  Agent Card
          - POST /                        JSON-RPC endpoint
          - GET  /health                  Health check
          - GET  /docs                    Swagger UI
        - MCP host: http://localhost:{MCP_PORT}/mcp/<server_name>/sse

        Args:
            agent_id: Agent ID for MCP data isolation
            user_id: User ID (defaults to agent_id)
            modules: Module names or classes (default: all_mcp_modules())
            api_host: A2A API host (default: "0.0.0.0")
            api_port: A2A API port (default: 8000)
        """
        user = user_id or agent_id

        processes = []

        logger.info("Starting XYZ Agent Context - Full Deployment")
        logger.info("   Protocol: A2A/0.3 (Google Agent-to-Agent)")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   User ID: {user}")
        # 启动 A2A API Server
        logger.info("Starting A2A Protocol API Server...")
        logger.info(f"   Endpoint: http://{api_host}:{api_port}")
        api_process = multiprocessing.Process(target=self._run_api_server, args=(api_host, api_port))
        api_process.start()
        processes.append(("A2A-API-Server", api_process, api_port))
        logger.info(f"   A2A API Server started (PID: {api_process.pid})")

        # The MCP host runs on THIS process's loop (one port, every module mounted).
        logger.info(f"Starting the MCP host on port {mcp_port()} (modules resolved after the plugin boot)...")
        logger.info("A2A API Endpoints:")
        logger.info(f"   GET  http://{api_host}:{api_port}/.well-known/agent.json")
        logger.info(f"   POST http://{api_host}:{api_port}/")
        logger.info(f"   GET  http://{api_host}:{api_port}/docs")
        logger.info("Press Ctrl+C to stop all services")
        try:
            asyncio.run(self.run_mcp_servers_async(agent_id=agent_id, user_id=user_id, modules=modules))
        except KeyboardInterrupt:
            logger.warning("Stopping all services...")
        finally:
            for name, process, _ in processes:
                process.terminate()
                logger.info(f"   Stopped {name}")
            logger.info("All services stopped")

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def list_available_modules(self) -> List[str]:
        """
        List all available modules that can be loaded.

        Returns:
            List of module names from module_registry
        """
        return list(module_registry.keys())

    def get_default_mcp_modules(self) -> List[str]:
        """
        Get the default list of MCP modules.

        Returns:
            List of default MCP module names
        """
        return all_mcp_modules().copy()


def _seam_uses_backend() -> bool:
    """True when ``NARRANEXUS_BACKEND_URL`` is set — the data-access seam runs in
    HttpStore mode, so this process forwards every DB-touching tool to the backend
    and opens NO MySQL pool of its own (and must not run migrations: the backend
    owns them in cloud, and the mcp container may hold no DB creds / DDL grant)."""
    import os

    return bool(os.environ.get("NARRANEXUS_BACKEND_URL", "").strip())


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry — also what the one-release ``xyz_agent_context`` path shims call."""
    import sys
    from narranexus.platform.utils.logging import setup_logging

    args = list(sys.argv[1:] if argv is None else argv)
    setup_logging("mcp")

    # Every command below reads the module roster (serve it, list it, print
    # it), and the roster is registry-derived: boot the plugin platform for the
    # mcp role once, up front. Idempotent, so run_mcp_servers_async() booting
    # again for programmatic callers is a no-op.
    from narranexus.platform.module_system.plugins_boot import boot_mcp_plugins

    boot_mcp_plugins()

    runner = ModuleRunner()

    def print_usage():
        available = runner.list_available_modules()
        defaults = runner.get_default_mcp_modules()

        print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    XYZ Agent Context - Module Runner                         ║
║                      A2A Protocol (Google Agent-to-Agent)                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

Usage: python -m narranexus.platform.module_system.module_runner [command] [options]

Commands:
  module     Run A2A API Server + all MCP Servers [Recommended]
  api        Run A2A Protocol API Server only
  mcp        Run all MCP Servers only
  list       List available modules
  <default>  Run all default MCP servers

Available Modules:
  {", ".join(available)}

Default MCP Modules:
  {", ".join(defaults)}

Examples:
  # Full deployment (A2A + MCP)
  python -m narranexus.platform.module_system.module_runner module

  # MCP servers only
  python -m narranexus.platform.module_system.module_runner mcp

  # A2A API only
  python -m narranexus.platform.module_system.module_runner api

  # List modules
  python -m narranexus.platform.module_system.module_runner list

A2A API Endpoints:
  GET  /.well-known/agent.json    Agent Card (service discovery)
  POST /                          JSON-RPC 2.0 endpoint
  GET  /health                    Health check
  GET  /docs                      Swagger UI

MCP host (one port, MCP_PORT, default 7801 — every module server mounted by path):
  - GET  http://localhost:7801/mcp/healthz
  - <module>: http://localhost:7801/mcp/<server_name>/sse   (Claude Code)
              http://localhost:7801/mcp/<server_name>/mcp   (Codex CLI)

Supported JSON-RPC Methods:
  - agentCard/get         Get Agent Card
  - tasks/send            Send message (sync)
  - tasks/sendSubscribe   Send message (SSE streaming)
  - tasks/get             Get task status
  - tasks/cancel          Cancel task
""")

    # Parse command line arguments
    if args:
        command = args[0].lower()

        if command == "module":
            # Full deployment: A2A API + MCP
            runner.run_module()
        elif command == "api":
            # A2A API Server only
            runner.run_api_server()
        elif command == "mcp" or command == "all":
            # All MCP servers in one process when we hold no MySQL pool (seam in
            # HttpStore mode, or SQLite) — saves the ~260 MB per-process import;
            # multi-process only when each process runs its own pool.
            asyncio.run(runner.run_mcp_servers_async())
        elif command == "list":
            # List available modules
            print("\n📦 Available Modules:")
            for name in runner.list_available_modules():
                is_default = "✓" if name in all_mcp_modules() else " "
                print(f"   [{is_default}] {name}")
            print("\n   ✓ = Included in default MCP deployment\n")
        elif command == "help" or command == "-h" or command == "--help":
            print_usage()
        else:
            print(f"❌ Unknown command: {command}")
            print_usage()
    else:
        # Default: run all MCP servers
        print("🚀 Starting default MCP servers...")
        print("   (Use 'module' command for full deployment with A2A API)\n")
        asyncio.run(runner.run_mcp_servers_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
