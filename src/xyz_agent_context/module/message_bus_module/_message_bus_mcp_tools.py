"""
@file_name: _message_bus_mcp_tools.py
@author: Bin Liang
@date: 2026-04-02
@description: MCP atomic tools for MessageBus operations (bus_* prefix)

These tools map to the MessageBusService interface methods.
The bus_ prefix avoids collision with matrix_*, slack_*, etc.

Each tool receives a get_message_bus_fn async callable that returns a
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
        get_message_bus_fn: Async callable that returns a MessageBusService instance.
    """

    @mcp.tool()
    async def bus_send_message(
        agent_id: str,
        channel_id: str,
        content: str,
        mention_list: str = "",
    ) -> dict:
        """
        Send a text message to a MessageBus channel.

        Use this tool to send messages to other agents in a channel you belong to.

        Args:
            agent_id: Your agent ID (the sender)
            channel_id: Target channel ID (e.g. "ch_a1b2c3d4")
            content: Message text to send
            mention_list: Comma-separated agent IDs to mention (e.g. "agent_abc,agent_def")

        Returns:
            Result dict with message_id on success, or error details
        """
        bus = await get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": "MessageBus not available"}

        try:
            mentions: Optional[List[str]] = None
            if mention_list.strip():
                mentions = [m.strip() for m in mention_list.split(",") if m.strip()]

            msg_id = await bus.send_message(
                from_agent=agent_id,
                to_channel=channel_id,
                content=content,
                mentions=mentions,
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
        bus = await get_message_bus_fn()
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
        bus = await get_message_bus_fn()
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
        bus = await get_message_bus_fn()
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
    async def bus_send_to_agent(
        agent_id: str,
        to_agent_id: str,
        content: str,
    ) -> dict:
        """
        Send a direct message to another agent by their agent_id.

        Auto-creates a direct channel between you and the target agent if one
        doesn't already exist. Use this when you want to contact a specific agent
        directly rather than through a shared channel.

        Args:
            agent_id: Your agent ID (the sender)
            to_agent_id: Target agent's ID (the recipient)
            content: Message text to send

        Returns:
            Result dict with message_id on success, or error details
        """
        bus = await get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": "MessageBus not available"}

        try:
            msg_id = await bus.send_to_agent(
                from_agent=agent_id,
                to_agent=to_agent_id,
                content=content,
            )
            return {"success": True, "message_id": msg_id, "to_agent": to_agent_id}
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
        bus = await get_message_bus_fn()
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

    @mcp.tool()
    async def bus_get_messages(agent_id: str, channel_id: str, limit: int = 50) -> dict:
        """Get message history from a channel.

        Args:
            agent_id: Your agent ID
            channel_id: Channel to retrieve messages from
            limit: Maximum number of messages (default 50)
        """
        try:
            bus = await get_message_bus_fn()
            if bus is None:
                return {"success": False, "error": "MessageBus not available"}
            messages = await bus.get_messages(channel_id, limit=limit)
            return {"success": True, "messages": [
                {"from": m.from_agent, "content": m.content, "time": str(m.created_at), "mentions": m.mentions}
                for m in messages
            ]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def bus_get_channel_members(agent_id: str, channel_id: str) -> dict:
        """Get all members of a channel.

        Args:
            agent_id: Your agent ID
            channel_id: Channel to inspect
        """
        try:
            bus = await get_message_bus_fn()
            if bus is None:
                return {"success": False, "error": "MessageBus not available"}
            members = await bus.get_channel_members(channel_id)
            return {"success": True, "members": [m.agent_id for m in members]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def bus_leave_channel(agent_id: str, channel_id: str) -> dict:
        """Leave a channel.

        Args:
            agent_id: Your agent ID
            channel_id: Channel to leave
        """
        try:
            bus = await get_message_bus_fn()
            if bus is None:
                return {"success": False, "error": "MessageBus not available"}
            await bus.leave_channel(agent_id, channel_id)
            return {"success": True, "message": f"Left channel {channel_id}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def bus_kick_member(agent_id: str, channel_id: str, target_agent_id: str) -> dict:
        """Remove another agent from a channel.

        Args:
            agent_id: Your agent ID (must be channel creator)
            channel_id: Channel to remove member from
            target_agent_id: Agent to remove
        """
        try:
            bus = await get_message_bus_fn()
            if bus is None:
                return {"success": False, "error": "MessageBus not available"}
            await bus.kick_member(channel_id, target_agent_id)
            return {"success": True, "message": f"Removed {target_agent_id} from {channel_id}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def bus_get_agent_profile(agent_id: str, target_agent_id: str) -> dict:
        """Get another agent's profile.

        Args:
            agent_id: Your agent ID
            target_agent_id: Agent whose profile to retrieve
        """
        try:
            bus = await get_message_bus_fn()
            if bus is None:
                return {"success": False, "error": "MessageBus not available"}
            profile = await bus.get_agent_profile(target_agent_id)
            if profile is None:
                return {"success": False, "error": f"Agent {target_agent_id} not found"}
            return {"success": True, "profile": {
                "agent_id": profile.agent_id, "owner": profile.owner_user_id,
                "capabilities": profile.capabilities, "description": profile.description,
                "visibility": profile.visibility,
            }}
        except Exception as e:
            return {"success": False, "error": str(e)}
