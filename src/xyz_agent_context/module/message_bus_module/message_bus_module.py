"""
@file_name: message_bus_module.py
@author: Bin Liang
@date: 2026-04-02
@description: MessageBusModule - Agent-to-agent communication via MessageBus

Replaces MatrixModule with a protocol-agnostic message bus. Provides MCP tools
for sending/receiving messages, managing channels, and discovering agents.
Works with any MessageBusService implementation (LocalMessageBus, CloudMessageBus).

Instance level: Agent-level (one per Agent, is_public=True).
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
)


# MCP server port for MessageBus tools
MESSAGE_BUS_MCP_PORT = 7820


class MessageBusModule(XYZBaseModule):
    """
    MessageBus communication module.

    Enables Agents to communicate with each other via the MessageBus service.
    Provides MCP tools for messaging, channel management, and agent discovery.

    Instance level: Agent-level (one per Agent, is_public=True).
    """

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        """Return Module configuration."""
        return ModuleConfig(
            name="MessageBusModule",
            priority=5,
            enabled=True,
            description=(
                "Agent-to-agent communication via message bus. "
                "Provides tools for sending/receiving messages, managing channels, "
                "and discovering other agents."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """Return MCP Server configuration for MessageBus tools."""
        return MCPServerConfig(
            server_name="message_bus_module",
            server_url=f"http://localhost:{MESSAGE_BUS_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        """Create MCP Server with MessageBus tools registered."""
        try:
            from fastmcp import FastMCP

            mcp = FastMCP("MessageBusModule MCP")
            mcp.settings.port = MESSAGE_BUS_MCP_PORT

            from ._message_bus_mcp_tools import register_message_bus_mcp_tools
            register_message_bus_mcp_tools(mcp, get_message_bus_fn=_get_default_bus)

            logger.info(
                f"MessageBusModule MCP server created on port {MESSAGE_BUS_MCP_PORT}"
            )
            return mcp

        except Exception as e:
            logger.error(f"Failed to create MessageBusModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """
        Return MessageBus-specific instructions for the system prompt.

        Includes information about unread messages and available tools.
        """
        unread_info = ctx_data.extra_data.get("bus_unread_messages", [])

        tools_section = """Available tools (prefix: bus_*):
- `bus_send_message`: Send a message to a channel
- `bus_create_channel`: Create a new channel and invite members
- `bus_search_agents`: Search for agents in the registry
- `bus_get_unread`: Get your unread messages
- `bus_register_agent`: Register yourself in the agent discovery registry"""

        instructions = f"""
#### MessageBus Communication

MessageBus is your **inter-agent messaging channel**. Use it to collaborate with
other Agents, exchange information, and coordinate tasks.

{tools_section}

##### When to Use MessageBus
- You need to **contact another Agent** (ask a question, share information)
- Your owner asks you to **send a message** to another agent
- You want to **discover agents** by capability or description
"""

        if unread_info:
            instructions += "\n##### Unread Messages\n"
            for msg in unread_info[:10]:
                instructions += (
                    f"- [{msg.get('from_agent', 'unknown')}] "
                    f"in channel {msg.get('channel_id', '?')}: "
                    f"{msg.get('content', '')[:80]}\n"
                )

        return instructions

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject unread MessageBus messages into agent context.

        Fetches unread messages via the MessageBusService and adds them
        to ctx_data.extra_data["bus_unread_messages"].
        """
        try:
            bus = _get_default_bus()
            if bus is None:
                return ctx_data

            unread = await bus.get_unread(self.agent_id)
            if unread:
                ctx_data.extra_data["bus_unread_messages"] = [
                    msg.model_dump() for msg in unread
                ]
        except Exception as e:
            logger.error(f"MessageBusModule hook_data_gathering failed: {e}")
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        """Post-execution cleanup for MessageBus (currently no-op)."""
        pass


# =============================================================================
# Module-level helper
# =============================================================================

_bus_instance: Optional[Any] = None


def _get_default_bus():
    """
    Get the default MessageBusService instance.

    Lazily initializes a LocalMessageBus with SQLiteBackend on first call.
    Returns None if initialization fails.
    """
    global _bus_instance
    if _bus_instance is not None:
        return _bus_instance

    try:
        from xyz_agent_context.message_bus import LocalMessageBus
        from xyz_agent_context.utils.db_backend import SQLiteBackend

        backend = SQLiteBackend()
        _bus_instance = LocalMessageBus(backend=backend)
        return _bus_instance
    except Exception as e:
        logger.error(f"Failed to initialize default MessageBus: {e}")
        return None
