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
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ModuleInstructions,
    ContextData,
    HookAfterExecutionParams,
)

# Import utils
from xyz_agent_context.utils import DatabaseClient
from xyz_agent_context.utils.mcp_executor import list_mcp_tools

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


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
    - get_instructions() - Return instructions to add to system prompt
    - get_mcp_config() - Return MCP Server configuration (if any)
    - create_mcp_server() - Create MCP Server instance (if any)

    Module data isolation:
    - Each Module's data is isolated by agent_id + user_id
    - Data is stored in respective database tables

    MCP database connection:
    - Each MCP server manages its own database connection
    - Use get_mcp_db_client() in MCP tools to get the connection
    - Connections are automatically cleaned up when MCP server shuts down
    """

    # MCP-specific database connection (class variable, independent per MCP process)
    _mcp_db_client: Optional["AsyncDatabaseClient"] = None

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
            instance_ids: All instance IDs associated with the Narrative (used for hook_data_gathering, etc.)
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
        Get the MCP-specific database client

        Each MCP server (independent process) manages its own database connection.
        This avoids issues with sharing connection pools across processes/event loops.

        Returns:
            AsyncDatabaseClient instance

        Example:
            @mcp.tool()
            async def my_tool(arg: str) -> str:
                db = await XYZBaseModule.get_mcp_db_client()
                result = await db.get_one("table", {"id": arg})
                return str(result)
        """
        if cls._mcp_db_client is None:
            from xyz_agent_context.utils.db_factory import get_db_client
            logger.info(f"Creating MCP-specific AsyncDatabaseClient for {cls.__name__}")
            cls._mcp_db_client = await get_db_client()
            logger.success(f"MCP AsyncDatabaseClient created for {cls.__name__}")
        return cls._mcp_db_client

    @classmethod
    async def close_mcp_db_client(cls) -> None:
        """
        Close the MCP-specific database client

        Typically called when the MCP server shuts down.
        """
        if cls._mcp_db_client is not None:
            logger.info(f"Closing MCP AsyncDatabaseClient for {cls.__name__}")
            await cls._mcp_db_client.close()
            cls._mcp_db_client = None
            logger.success(f"MCP AsyncDatabaseClient closed for {cls.__name__}")

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
        mcp_config = await self.get_mcp_config()
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

    async def get_instructions(self, ctx_data: ContextData) -> str:
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

    @abstractmethod
    def get_config(self) -> ModuleConfig:
        """
        Return Module configuration

        Each Module must implement this method to define its own configuration

        Returns:
            ModuleConfig
        """
        pass

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
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

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        """
        After event execution, perform processing such as updating database, updating context, Memory summarization and other async operations.

        Args:
            params: HookAfterExecutionParams, containing:
                - execution_ctx: Execution context (event_id, agent_id, user_id, working_source)
                - io_data: Input/output (input_content, final_output)
                - trace: Execution trace (event_log, agent_loop_response)
                - ctx_data: Complete context data
        """
        return None

    # =========================================================================
    # MCP Server
    # =========================================================================

    @abstractmethod
    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
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
