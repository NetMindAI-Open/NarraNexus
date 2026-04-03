"""
@file_name: _message_bus_mcp_tools.py
@author: Bin Liang
@date: 2026-04-02
@description: MCP atomic tools for MessageBus operations (bus_* prefix)

These tools map to the MessageBusService interface methods.
The bus_ prefix avoids collision with matrix_*, slack_*, etc.

Each tool receives a get_message_bus_fn callable that returns a
MessageBusService instance, following the project pattern for
dependency injection in MCP tool modules.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional


def register_message_bus_mcp_tools(
    mcp: Any,
    get_message_bus_fn: Callable,
) -> None:
    """
    Register all MessageBus MCP tools on the given MCP server instance.

    Called by MessageBusModule.create_mcp_server().

    Args:
        mcp: The FastMCP server instance.
        get_message_bus_fn: Callable that returns a MessageBusService instance.
    """

    @mcp.tool()
    async def bus_send_message(
        agent_id: str,
        channel_id: str,
        content: str,
    ) -> dict:
        """
        Send a text message to a MessageBus channel.

        Use this tool to send messages to other agents in a channel you belong to.

        Args:
            agent_id: Your agent ID (the sender)
            channel_id: Target channel ID (e.g. "ch_a1b2c3d4")
            content: Message text to send

        Returns:
            Result dict with message_id on success, or error details
        """
        bus = get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": "MessageBus not available"}

        try:
            msg_id = await bus.send_message(
                from_agent=agent_id,
                to_channel=channel_id,
                content=content,
            )
            return {"success": True, "message_id": msg_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def bus_create_channel(
        agent_id: str,
        name: str,
        members: str,
    ) -> dict:
        """
        Create a new MessageBus channel and add members.

        The agent_id (you) will be included as a member automatically.
        Pass additional member agent IDs as a comma-separated string.

        Args:
            agent_id: Your agent ID (will be added as first member)
            name: Human-readable channel name (e.g. "Project Discussion")
            members: Comma-separated agent IDs to invite
                     (e.g. "agent_abc,agent_def")

        Returns:
            Result dict with channel_id on success
        """
        bus = get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": "MessageBus not available"}

        try:
            member_list = [m.strip() for m in members.split(",") if m.strip()]
            # Ensure the creator is included
            if agent_id not in member_list:
                member_list.insert(0, agent_id)

            channel_id = await bus.create_channel(
                name=name,
                members=member_list,
            )
            return {"success": True, "channel_id": channel_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def bus_search_agents(
        query: str,
    ) -> dict:
        """
        Search for agents in the MessageBus registry.

        Use this to discover other agents by capability or description.

        Args:
            query: Search query (matched against capabilities and description)

        Returns:
            Result dict with matching agents list
        """
        bus = get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": "MessageBus not available"}

        try:
            results = await bus.search_agents(query=query)
            return {
                "success": True,
                "agents": [a.model_dump() for a in results],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def bus_get_unread(
        agent_id: str,
    ) -> dict:
        """
        Get all unread messages for your agent across all channels.

        Returns messages that have been sent since you last read each channel.

        Args:
            agent_id: Your agent ID

        Returns:
            Result dict with unread messages list
        """
        bus = get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": "MessageBus not available"}

        try:
            messages = await bus.get_unread(agent_id)
            return {
                "success": True,
                "messages": [m.model_dump() for m in messages],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def bus_register_agent(
        agent_id: str,
        capabilities: str,
        description: str,
    ) -> dict:
        """
        Register your agent in the MessageBus discovery registry.

        Other agents can then find you via bus_search_agents.
        Call this to make yourself discoverable or to update your profile.

        Args:
            agent_id: Your agent ID
            capabilities: Comma-separated capability tags
                          (e.g. "chat,data_analysis,research")
            description: Human-readable description of what you do

        Returns:
            Result dict indicating success or failure
        """
        bus = get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": "MessageBus not available"}

        try:
            cap_list = [c.strip() for c in capabilities.split(",") if c.strip()]
            await bus.register_agent(
                agent_id=agent_id,
                owner_user_id="",  # Will be filled in by the caller context
                capabilities=cap_list,
                description=description,
            )
            return {"success": True, "message": "Agent registered successfully"}
        except Exception as e:
            return {"success": False, "error": str(e)}
