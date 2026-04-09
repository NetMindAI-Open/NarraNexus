"""
@file_name: message_bus_module.py
@author: NarraNexus
@date: 2026-04-02
@description: MessageBusModule - Agent-to-agent communication via MessageBus

Replaces MatrixModule with a protocol-agnostic message bus. Provides MCP tools
for sending/receiving messages, managing channels, and discovering agents.

Instance level: Agent-level (one per Agent, is_public=True).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
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
        return MCPServerConfig(
            server_name="message_bus_module",
            server_url=f"http://localhost:{MESSAGE_BUS_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        try:
            from fastmcp import FastMCP

            mcp = FastMCP("MessageBusModule MCP")
            mcp.settings.port = MESSAGE_BUS_MCP_PORT

            from ._message_bus_mcp_tools import register_message_bus_mcp_tools
            register_message_bus_mcp_tools(mcp, get_message_bus_fn=_get_default_bus_async)

            logger.info(f"MessageBusModule MCP server created on port {MESSAGE_BUS_MCP_PORT}")
            return mcp
        except Exception as e:
            logger.error(f"Failed to create MessageBusModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        parts = [
            "## MessageBus — Agent Communication",
            "",
            "You can communicate with other agents via the MessageBus.",
            "",
            "### Available Tools",
            "- **bus_send_message**: Send a message to a channel (supports @mentions via mention_list)",
            "- **bus_send_to_agent**: Direct message another agent by agent_id",
            "- **bus_create_channel**: Create a group or direct channel",
            "- **bus_get_messages**: Get message history from a channel",
            "- **bus_get_unread**: Get all your unread messages",
            "- **bus_get_channel_members**: List members of a channel",
            "- **bus_leave_channel**: Leave a channel",
            "- **bus_kick_member**: Remove another agent from a channel",
            "- **bus_get_agent_profile**: View another agent's profile",
            "- **bus_search_agents**: Search for agents by capability or description",
            "- **bus_register_agent**: Register or update your agent profile",
            "",
            "### Mention Rules",
            "- In group channels, agents only receive messages that @mention them",
            "- In direct channels, all messages are delivered",
            "- Use mention_list='@everyone' to notify all channel members",
            "- Use mention_list='agent_id1,agent_id2' to mention specific agents",
        ]

        # Add known agents
        known = ctx_data.extra_data.get("bus_known_agents", [])
        if known:
            parts.append("")
            parts.append("### Known Agents")
            for a in known:
                parts.append(f"- **{a.get('agent_id', '')}**: {a.get('description', 'No description')}")

        # Add channels
        channels = ctx_data.extra_data.get("bus_channels", [])
        if channels:
            parts.append("")
            parts.append("### Your Channels")
            for ch in channels:
                parts.append(f"- {ch.get('channel_id', '')}: {ch.get('name', 'unnamed')}")

        # Add unread count
        unread = ctx_data.extra_data.get("bus_unread_messages", [])
        if unread:
            parts.append("")
            parts.append(f"### Unread Messages: {len(unread)}")

        return "\n".join(parts)

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject MessageBus context into agent data.

        1. Auto-register current agent in bus_agent_registry
        2. Fetch all known agents (from agents DB table + bus_agent_registry)
        3. Fetch unread messages
        4. Fetch channel list
        """
        try:
            bus = await _get_default_bus_async()
            if bus is None:
                return ctx_data

            # --- 1. Auto-register this agent in bus_agent_registry ---
            try:
                db = await _get_shared_db()
                if db:
                    # Get agent info from agents table
                    agent_row = await db.get_one("agents", {"agent_id": self.agent_id})
                    if agent_row:
                        owner = agent_row.get("created_by", "")
                        name = agent_row.get("agent_name", "")
                        desc = agent_row.get("agent_description", "")
                        is_public = agent_row.get("is_public", 0)
                        await bus.register_agent(
                            agent_id=self.agent_id,
                            owner_user_id=owner,
                            capabilities=[],
                            description=f"{name}: {desc}" if desc else name,
                            visibility="public" if is_public else "private",
                        )
            except Exception as e:
                logger.debug(f"Failed to auto-register agent in bus: {e}")

            # --- 2. Fetch all known agents ---
            known_agents = []
            try:
                db = await _get_shared_db()
                if db:
                    # Get all agents from the agents table (most reliable source)
                    all_agents = await db.get("agents", {})
                    for a in all_agents:
                        if a.get("agent_id") != self.agent_id:  # Exclude self
                            known_agents.append({
                                "agent_id": a.get("agent_id"),
                                "agent_name": a.get("agent_name", ""),
                                "agent_description": a.get("agent_description", ""),
                                "is_public": a.get("is_public", 0),
                                "created_by": a.get("created_by", ""),
                            })
                if known_agents:
                    ctx_data.extra_data["known_agents"] = known_agents
            except Exception as e:
                logger.debug(f"Failed to fetch known agents: {e}")

            # --- 3. Fetch unread messages ---
            try:
                unread = await bus.get_unread(self.agent_id)
                if unread:
                    ctx_data.extra_data["bus_unread_messages"] = [
                        msg.model_dump() for msg in unread
                    ]
            except Exception as e:
                logger.debug(f"Failed to fetch unread messages: {e}")

            # --- 4. Fetch channels ---
            try:
                rows = await bus._db.execute(
                    "SELECT c.* FROM bus_channels c "
                    "JOIN bus_channel_members cm ON c.channel_id = cm.channel_id "
                    "WHERE cm.agent_id = %s",
                    (self.agent_id,),
                )
                if rows:
                    ctx_data.extra_data["bus_channels"] = [dict(r) for r in rows]
            except Exception as e:
                logger.debug(f"Failed to load bus channels: {e}")

        except Exception as e:
            logger.error(f"MessageBusModule hook_data_gathering failed: {e}")
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        if params.working_source != WorkingSource.MESSAGE_BUS:
            return

        try:
            bus = await _get_default_bus_async()
            if bus is None:
                return

            unread = await bus.get_unread(self.agent_id)
            if unread:
                msg_ids = [m.message_id for m in unread]
                await bus.mark_read(self.agent_id, msg_ids)
                logger.info(
                    f"MessageBusModule: marked {len(msg_ids)} messages as read "
                    f"for agent {self.agent_id}"
                )
        except Exception as e:
            logger.error(f"MessageBusModule hook_after_event_execution failed: {e}")


# =============================================================================
# Module-level helpers
# =============================================================================

_bus_instances: dict[int, Any] = {}  # keyed by event loop id


async def _get_default_bus_async():
    """Get or create a LocalMessageBus bound to the current event loop.

    Uses ``get_db_client()`` which already handles event-loop changes and
    creates a fresh aiomysql pool on the correct loop.  The bus instance is
    cached per-loop so subsequent calls on the same loop are free.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    if loop_id in _bus_instances:
        return _bus_instances[loop_id]

    try:
        from xyz_agent_context.message_bus import LocalMessageBus
        from xyz_agent_context.utils.db_factory import get_db_client

        db = await get_db_client()
        backend = db._backend
        if backend is None:
            logger.warning("MessageBus: database backend is None")
            return None

        bus = LocalMessageBus(backend=backend)
        _bus_instances[loop_id] = bus
        logger.info(f"MessageBus: created instance for event loop {loop_id}")
        return bus
    except Exception as e:
        logger.error(f"Failed to initialize default MessageBus: {e}")
        return None


def _get_default_bus():
    """Sync wrapper — only works if a bus was already created for the current loop."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        return _bus_instances.get(id(loop))
    except RuntimeError:
        return None


async def _get_shared_db():
    """Get the shared AsyncDatabaseClient."""
    try:
        from xyz_agent_context.utils.db_factory import get_db_client
        return await get_db_client()
    except Exception as e:
        logger.debug(f"Failed to get shared DB client: {e}")
        return None
