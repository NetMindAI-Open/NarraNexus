"""
Module Runner - Deploy A2A API Server and MCP Servers

@file_name: module_runner.py
@author: NetMind.AI
@date: 2025-11-07
@description: Unified service deployment for MCP servers and A2A API

=============================================================================
Supported Running Modes
=============================================================================

1. run_mcp_server(module)
   Run a single module's MCP Server

2. run_all_mcp_servers(agent_id, user_id, modules)
   Run all modules' MCP Servers in separate processes

3. run_mcp_servers_async(agent_id, user_id, modules)  [NEW]
   Run all MCP servers in a single process using asyncio

4. run_api_server(host, port)
   Run A2A Protocol API Server

5. run_module(agent_id, modules, api_host, api_port)  [Recommended]
   Run A2A API Server and all MCP Servers together

=============================================================================
Usage Examples
=============================================================================

CLI:
    python -m xyz_agent_context.module.module_runner module  # Full deployment
    python -m xyz_agent_context.module.module_runner api     # API only
    python -m xyz_agent_context.module.module_runner mcp     # MCP only

Python:
    from xyz_agent_context.module.module_runner import ModuleRunner

    runner = ModuleRunner()
    runner.run_module()  # Full deployment

    # Or run specific modules
    runner.run_all_mcp_servers(
        agent_id="my_agent",
        user_id="my_user",
        modules=["AwarenessModule", "JobModule"]
    )

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
    ┌───────────┐          ┌───────────┐          ┌───────────┐
    │ A2A API   │          │ Awareness │          │   Job     │
    │ Server    │          │ MCP       │          │   MCP     │
    │ :8000     │          │ :7801     │          │   :7803   │
    └───────────┘          └───────────┘          └───────────┘
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
import multiprocessing
from typing import Any, List, Optional, Type, Union

from loguru import logger

# Module (same package)
from xyz_agent_context.module import XYZBaseModule, MODULE_MAP
from xyz_agent_context.module.awareness_module.awareness_module import AwarenessModule
from xyz_agent_context.module.social_network_module import SocialNetworkModule
from xyz_agent_context.module.job_module.job_module import JobModule

# Utils
from xyz_agent_context.utils import DatabaseClient, get_db_client, get_db_client_sync


# =============================================================================
# Default Configuration
# =============================================================================

# Modules that have MCP servers and their fixed ports.
DEFAULT_MCP_MODULES = [
    "AwarenessModule",      # port: 7801
    "ChatModule",           # port: 7804
    "SocialNetworkModule",  # port: 7802
    "JobModule",            # port: 7803
    "SkillModule",          # port: 7806
    "MessageBusModule",     # port: 7820
    "LarkModule",           # port: 7830
]

# Port reference (for documentation only - actual ports are set in each module)
MODULE_PORTS = {
    "AwarenessModule": 7801,
    "ChatModule": 7804,
    "SocialNetworkModule": 7802,
    "JobModule": 7803,
    "SkillModule": 7806,
    "MessageBusModule": 7820,
    "LarkModule": 7830,
}


class ModuleRunner:
    """
    Module Runner - Deploy and manage MCP Servers and A2A API.

    Features:
    - Run single or multiple MCP servers
    - Support both multiprocessing and asyncio modes
    - Automatic module discovery from MODULE_MAP
    - Flexible configuration (module names or classes)

    Usage:
        runner = ModuleRunner()

        # Run all default MCP servers
        runner.run_all_mcp_servers()

        # Run specific modules by name
        runner.run_all_mcp_servers(
            agent_id="my_agent",
            modules=["AwarenessModule", "JobModule"]
        )

        # Run with full deployment (A2A + MCP)
        runner.run_module()
    """

    def __init__(self):
        self.db = get_db_client_sync()

    # =========================================================================
    # Module Resolution
    # =========================================================================

    def _resolve_modules(
        self,
        modules: Optional[Union[List[str], List[Type[XYZBaseModule]]]] = None
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
            classes = runner._resolve_modules(None)  # Uses DEFAULT_MCP_MODULES
        """
        if modules is None:
            modules = DEFAULT_MCP_MODULES

        resolved = []
        for module in modules:
            if isinstance(module, str):
                # Resolve by name from MODULE_MAP
                if module not in MODULE_MAP:
                    logger.warning(f"Module '{module}' not found in MODULE_MAP, skipping")
                    continue
                resolved.append(MODULE_MAP[module])
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
        db_client: Optional[DatabaseClient] = None
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
    # Single MCP Server
    # =========================================================================

    def run_mcp_server(self, module: XYZBaseModule) -> None:
        """
        Run a single module's MCP server.

        Args:
            module: Module instance with MCP server capability

        Raises:
            ValueError: If module doesn't have an MCP server
        """
        mcp_server = module.create_mcp_server()
        if mcp_server is not None:
            logger.info(f"Starting MCP server for {module.__class__.__name__}")
            # FastMCP's __init__ hardcodes host=127.0.0.1 and auto-enables
            # DNS rebinding protection when host is localhost. In a multi-
            # container deployment (Docker compose on EC2, MySQL mode that
            # routes here via multiprocessing), that blocks backend/poller/
            # jobs/bus/lark from reaching MCP via the `mcp` service name.
            # Mirror the fix that _run_mcp_in_thread applies in async mode.
            mcp_server.settings.host = "0.0.0.0"
            from mcp.server.transport_security import TransportSecuritySettings
            mcp_server.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )
            mcp_server.run("sse")
        else:
            raise ValueError(f"Module {module.__class__.__name__} does not have an MCP server")

    @staticmethod
    def _run_single_mcp(module_class, agent_id: str, user_id: Optional[str] = None):
        """Run a single MCP server in an independent process."""
        db = get_db_client_sync()
        user = user_id or agent_id
        module = module_class(agent_id=agent_id, user_id=user, database_client=db)
        runner = ModuleRunner()
        runner.run_mcp_server(module)

    # =========================================================================
    # Multiple MCP Servers (Multiprocessing)
    # =========================================================================

    def run_all_mcp_servers(
        self,
        agent_id: str = "mcp_deploy",
        user_id: Optional[str] = None,
        modules: Optional[Union[List[str], List[Type[XYZBaseModule]]]] = None
    ) -> None:
        """
        Run all MCP servers in separate processes.

        Each module runs in its own process for isolation.

        Args:
            agent_id: Agent ID for data isolation
            user_id: User ID (defaults to agent_id)
            modules: List of module names or classes (default: DEFAULT_MCP_MODULES)

        Example:
            runner = ModuleRunner()

            # Run all default modules
            runner.run_all_mcp_servers()

            # Run specific modules
            runner.run_all_mcp_servers(
                agent_id="my_agent",
                modules=["AwarenessModule", "JobModule"]
            )
        """
        module_classes = self._resolve_modules(modules)

        if not module_classes:
            logger.warning("No modules to run")
            return

        user = user_id or agent_id
        processes = []

        logger.info("=" * 80)
        logger.info("Starting MCP Servers")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   User ID: {user}")
        logger.info("=" * 80)

        for i, module_class in enumerate(module_classes):
            module_name = module_class.__name__
            port = MODULE_PORTS.get(module_name, 7800 + i)
            logger.info(f"  Starting {module_name} (port: {port})...")

            # Create independent process
            process = multiprocessing.Process(
                target=self._run_single_mcp,
                args=(module_class, agent_id, user_id)
            )
            process.start()
            processes.append((module_name, process, port))
            logger.info(f"  {module_name} started (PID: {process.pid})")

        logger.info("=" * 80)
        logger.info(f"{len(module_classes)} MCP servers started")
        logger.info("MCP Server Endpoints:")
        for name, _, port in processes:
            logger.info(f"   - {name}: http://localhost:{port}/sse")
        logger.info("=" * 80)
        logger.info("Press Ctrl+C to stop all servers")

        try:
            for name, process, _ in processes:
                process.join()
        except KeyboardInterrupt:
            logger.warning("Stopping all MCP servers...")
            for name, process, _ in processes:
                process.terminate()
                logger.info(f"  Stopped {name}")
            logger.info("All MCP servers stopped")

    # =========================================================================
    # Multiple MCP Servers (Asyncio - Single Process)
    # =========================================================================

    async def run_mcp_servers_async(
        self,
        agent_id: str = "mcp_deploy",
        user_id: Optional[str] = None,
        modules: Optional[Union[List[str], List[Type[XYZBaseModule]]]] = None
    ) -> None:
        """
        Run multiple MCP servers concurrently in a single process using asyncio.

        This is more lightweight than multiprocessing but all servers share
        the same process. Useful for development or resource-constrained environments.

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
        module_classes = self._resolve_modules(modules)

        if not module_classes:
            logger.error("No modules to run")
            return

        user = user_id or agent_id
        db = await get_db_client()

        # Ensure all tables exist (MCP runs as separate process)
        from xyz_agent_context.utils.schema_registry import auto_migrate
        await auto_migrate(db._backend)
        logger.info("Schema auto-migration complete")

        logger.info("=" * 60)
        logger.info("🚀 Starting MCP Servers (async mode)")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   User ID: {user}")
        logger.info("=" * 60)

        # Create module instances
        instances = []
        for module_class in module_classes:
            module = module_class(agent_id=agent_id, user_id=user, database_client=db)
            mcp_server = module.create_mcp_server()
            if mcp_server:
                instances.append((module_class.__name__, mcp_server))
                logger.info(f"  ✅ {module_class.__name__} ready")
            else:
                logger.warning(f"  ⚠️ {module_class.__name__} has no MCP server")

        if not instances:
            logger.error("No MCP servers to run")
            return

        logger.info(f"\n✅ {len(instances)} MCP servers ready to start")

        # Run all MCP servers in threads within a single process.
        # This shares one SQLite connection, avoiding cross-process write lock contention.
        import threading

        threads: list[threading.Thread] = []
        for module_name, mcp_server in instances:
            port = MODULE_PORTS.get(module_name, 7800 + len(threads))
            logger.info(f"  🚀 {module_name} → http://localhost:{port}/sse")

            t = threading.Thread(
                target=self._run_mcp_in_thread,
                args=(mcp_server, module_name, port),
                daemon=True,
            )
            t.start()
            threads.append(t)

        logger.info(f"\n✅ {len(threads)} MCP servers running (single-process, threaded)")

        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            logger.info("Stopping MCP servers...")

    @staticmethod
    def _run_mcp_in_thread(mcp_server: Any, module_name: str, port: int) -> None:
        """Run a single MCP server in its own thread with a fresh event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            mcp_server.settings.host = "0.0.0.0"
            mcp_server.settings.port = port
            # Disable DNS rebinding protection so other containers can
            # reach MCP servers via Docker service name (e.g. "mcp:7801").
            # FastMCP auto-enables this when host is 127.0.0.1 at init
            # time, and changing host afterward doesn't clear it.
            from mcp.server.transport_security import TransportSecuritySettings
            mcp_server.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )
            mcp_server.run(transport="sse")
        except Exception as e:
            logger.error(f"MCP server {module_name} crashed: {e}")

    # ============================================================================= A2A API Server
    @staticmethod
    def _run_api_server(host: str, port: int):
        """
        Run A2A API server in an independent process

        Args:
            host: Host address
            port: Port number
        """
        from xyz_agent_context.module.chat_module.chat_trigger import A2AServer

        server = A2AServer(host=host, port=port)
        server.run()

    def run_api_server(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        agent_name: str = "XYZ Agent",
        agent_description: str = "XYZ Agent Context - Intelligent Conversational Agent"
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
        from xyz_agent_context.module.chat_module.chat_trigger import A2AServer

        logger.info("=" * 80)
        logger.info("Starting A2A Protocol API Server...")
        logger.info(f"   Agent: {agent_name}")
        logger.info(f"   Host: {host}")
        logger.info(f"   Port: {port}")
        logger.info("   Protocol: A2A/0.3 (Google Agent-to-Agent)")
        logger.info("=" * 80)

        server = A2AServer(
            host=host,
            port=port,
            agent_name=agent_name,
            agent_description=agent_description
        )
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
        api_port: int = 8000
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
        - MCP: http://localhost:{MCP_BASE_PORT + i}/sse

        Args:
            agent_id: Agent ID for MCP data isolation
            user_id: User ID (defaults to agent_id)
            modules: Module names or classes (default: DEFAULT_MCP_MODULES)
            api_host: A2A API host (default: "0.0.0.0")
            api_port: A2A API port (default: 8000)
        """
        module_classes = self._resolve_modules(modules)
        user = user_id or agent_id

        processes = []

        logger.info("=" * 80)
        logger.info("Starting XYZ Agent Context - Full Deployment")
        logger.info("   Protocol: A2A/0.3 (Google Agent-to-Agent)")
        logger.info(f"   Agent ID: {agent_id}")
        logger.info(f"   User ID: {user}")
        logger.info("=" * 80)

        # 启动 A2A API Server
        logger.info("Starting A2A Protocol API Server...")
        logger.info(f"   Endpoint: http://{api_host}:{api_port}")
        api_process = multiprocessing.Process(
            target=self._run_api_server,
            args=(api_host, api_port)
        )
        api_process.start()
        processes.append(("A2A-API-Server", api_process, api_port))
        logger.info(f"   A2A API Server started (PID: {api_process.pid})")

        # 启动所有 MCP Servers
        logger.info(f"Starting {len(module_classes)} MCP Servers...")
        for i, module_class in enumerate(module_classes):
            module_name = module_class.__name__
            port = MODULE_PORTS.get(module_name, 7800 + i)
            logger.info(f"   Starting {module_name} (port: {port})...")

            process = multiprocessing.Process(
                target=self._run_single_mcp,
                args=(module_class, agent_id, user_id)
            )
            process.start()
            processes.append((module_name, process, port))
            logger.info(f"   {module_name} started (PID: {process.pid})")

        logger.info("=" * 80)
        logger.info("Deployment Complete!")
        logger.info("A2A API Endpoints:")
        logger.info(f"   GET  http://{api_host}:{api_port}/.well-known/agent.json")
        logger.info(f"   POST http://{api_host}:{api_port}/")
        logger.info(f"   GET  http://{api_host}:{api_port}/docs")
        logger.info(f"MCP Servers ({len(module_classes)} running):")
        for i, module_class in enumerate(module_classes):
            module_name = module_class.__name__
            port = MODULE_PORTS.get(module_name, 7800 + i)
            logger.info(f"   - {module_name}: http://localhost:{port}/sse")
        logger.info("=" * 80)
        logger.info("Press Ctrl+C to stop all services")

        try:
            for name, process, _ in processes:
                process.join()
        except KeyboardInterrupt:
            logger.warning("Stopping all services...")
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
            List of module names from MODULE_MAP
        """
        return list(MODULE_MAP.keys())

    def get_default_mcp_modules(self) -> List[str]:
        """
        Get the default list of MCP modules.

        Returns:
            List of default MCP module names
        """
        return DEFAULT_MCP_MODULES.copy()


def _is_sqlite_mode() -> bool:
    """Check if the current DATABASE_URL points to SQLite."""
    import os
    url = os.environ.get("DATABASE_URL", "")
    return url.startswith("sqlite") or not url


if __name__ == "__main__":
    import sys
    from xyz_agent_context.utils.service_logger import setup_service_logger
    setup_service_logger("mcp")

    runner = ModuleRunner()

    def print_usage():
        available = runner.list_available_modules()
        defaults = runner.get_default_mcp_modules()

        print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    XYZ Agent Context - Module Runner                         ║
║                      A2A Protocol (Google Agent-to-Agent)                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

Usage: python -m xyz_agent_context.module.module_runner [command] [options]

Commands:
  module     Run A2A API Server + all MCP Servers [Recommended]
  api        Run A2A Protocol API Server only
  mcp        Run all MCP Servers only
  list       List available modules
  <default>  Run all default MCP servers

Available Modules:
  {', '.join(available)}

Default MCP Modules:
  {', '.join(defaults)}

Examples:
  # Full deployment (A2A + MCP)
  python -m xyz_agent_context.module.module_runner module

  # MCP servers only
  python -m xyz_agent_context.module.module_runner mcp

  # A2A API only
  python -m xyz_agent_context.module.module_runner api

  # List modules
  python -m xyz_agent_context.module.module_runner list

A2A API Endpoints:
  GET  /.well-known/agent.json    Agent Card (service discovery)
  POST /                          JSON-RPC 2.0 endpoint
  GET  /health                    Health check
  GET  /docs                      Swagger UI

MCP Servers (default ports):
  - AwarenessModule:     http://localhost:7801/sse
  - SocialNetworkModule: http://localhost:7802/sse
  - JobModule:           http://localhost:7803/sse
  - ChatModule:          http://localhost:7804/sse
  - SkillModule:         http://localhost:7806/sse
  - MessageBusModule:    http://localhost:7820/sse
  - LarkModule:          http://localhost:7830/sse

Supported JSON-RPC Methods:
  - agentCard/get         Get Agent Card
  - tasks/send            Send message (sync)
  - tasks/sendSubscribe   Send message (SSE streaming)
  - tasks/get             Get task status
  - tasks/cancel          Cancel task
""")

    # Parse command line arguments
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()

        if command == "module":
            # Full deployment: A2A API + MCP
            runner.run_module()
        elif command == "api":
            # A2A API Server only
            runner.run_api_server()
        elif command == "mcp" or command == "all":
            # All MCP servers — use async (single-process) mode for SQLite
            # to avoid multi-process write lock contention
            if _is_sqlite_mode():
                import asyncio
                asyncio.run(runner.run_mcp_servers_async())
            else:
                runner.run_all_mcp_servers()
        elif command == "list":
            # List available modules
            print("\n📦 Available Modules:")
            for name in runner.list_available_modules():
                is_default = "✓" if name in DEFAULT_MCP_MODULES else " "
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
        if _is_sqlite_mode():
            import asyncio
            asyncio.run(runner.run_mcp_servers_async())
        else:
            runner.run_all_mcp_servers()