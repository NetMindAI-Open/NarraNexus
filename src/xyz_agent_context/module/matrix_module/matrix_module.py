"""
@file_name: matrix_module.py
@author: Bin Liang
@date: 2026-03-10
@description: MatrixModule — first IM channel module implementation

MatrixModule integrates NexusMatrix Server as a system-level Module (not a Skill).
It provides:
- MCP Tools: matrix_send_message, matrix_create_room, matrix_search_agents, etc.
- Hooks: data_gathering (inject Matrix context), after_execution (mark_read)
- Trigger: MatrixTrigger (separate background process for message polling)

MatrixModule is a peer of JobModule and ChatModule — it hooks into the AgentRuntime
pipeline via the standard Module lifecycle.
"""

from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
)
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry

from ._matrix_hooks import matrix_hook_data_gathering, matrix_hook_after_event_execution
from ._matrix_credential_manager import MatrixCredentialManager
from .matrix_client import NexusMatrixClient


def _extract_matrix_id(contact_info: dict) -> str:
    """
    Extract matrix user ID from contact_info.

    Supports both canonical and legacy formats:
    - {"channels": {"matrix": {"id": "@xxx:localhost"}}}        (canonical)
    - {"channels": {"matrix": {"user_id": "@xxx:localhost"}}}   (legacy)
    - {"matrix": "@xxx:localhost"}                               (legacy)
    """
    if not contact_info:
        return ""
    channels = contact_info.get("channels", {})
    if isinstance(channels, dict):
        matrix_ch = channels.get("matrix", {})
        if isinstance(matrix_ch, dict):
            return matrix_ch.get("id", "") or matrix_ch.get("user_id", "")
        elif isinstance(matrix_ch, str):
            return matrix_ch
    # Legacy: top-level "matrix" key
    matrix_val = contact_info.get("matrix", "")
    if isinstance(matrix_val, str) and matrix_val:
        return matrix_val
    return ""


# Default NexusMatrix server URL (local deployment)
DEFAULT_SERVER_URL = "http://localhost:8953"

# MCP server port for Matrix tools
MATRIX_MCP_PORT = 7810


class MatrixModule(XYZBaseModule):
    """
    Matrix communication module.

    Enables Agents to communicate with each other via the NexusMatrix Server
    (Matrix protocol). Provides MCP tools for messaging, room management,
    and agent discovery.

    Instance level: Agent-level (one per Agent, is_public=True).
    """

    # Class-level flag to ensure channel sender is registered only once
    _sender_registered: bool = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Register Matrix channel sender on first instantiation
        if not MatrixModule._sender_registered:
            MatrixModule.register_channel_sender()
            MatrixModule._sender_registered = True

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        """Return Module configuration."""
        return ModuleConfig(
            name="MatrixModule",
            priority=5,  # Lower priority than Chat/SocialNetwork, higher than RAG
            enabled=True,
            description=(
                "Matrix communication channel for inter-Agent messaging. "
                "Provides tools for sending/receiving messages, managing rooms, "
                "and discovering other agents via NexusMatrix Server."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """Return MCP Server configuration for Matrix tools."""
        return MCPServerConfig(
            server_name="matrix_module",
            server_url=f"http://localhost:{MATRIX_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        """Create MCP Server with Matrix tools registered."""
        try:
            from fastmcp import FastMCP

            mcp = FastMCP(
                "MatrixModule MCP",
            )
            mcp.settings.port = MATRIX_MCP_PORT

            # Register all matrix_* tools
            from ._matrix_mcp_tools import register_matrix_mcp_tools
            register_matrix_mcp_tools(mcp)

            logger.info(f"MatrixModule MCP server created on port {MATRIX_MCP_PORT}")
            return mcp

        except Exception as e:
            logger.error(f"Failed to create MatrixModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """
        Return Matrix-specific instructions for the system prompt.

        When credentials exist: show full Matrix tools and context.
        When credentials are missing: only show matrix_register tool.
        """
        # Check if Matrix info was injected by hook_data_gathering
        matrix_info = ctx_data.extra_data.get("matrix_info")

        # Common tool list (always available regardless of credential state)
        tools_section = """Available tools (prefix: matrix_*):
- `matrix_register`: Register (or re-register) on NexusMatrix Server
- `matrix_send_message`: Send a message to a room
- `matrix_create_room`: Create a new DM or group room and invite users
- `matrix_invite_to_room`: Invite a user to an existing room
- `matrix_join_room`: Join a room you've been invited to
- `matrix_list_rooms`: List your joined rooms
- `matrix_get_room_members`: Get members of a room
- `matrix_search_agents`: Search for agents in the registry
- `matrix_get_agent_profile`: View another agent's profile"""

        if not matrix_info:
            # No credentials — show full tools but guide to register first
            return f"""
## Matrix Communication
You are NOT yet registered on NexusMatrix Server. You need to call `matrix_register` first before using other Matrix tools.

{tools_section}

**First step**: Call `matrix_register` to get your Matrix account. After that, you can use all other tools above.
"""

        matrix_id = matrix_info.get("matrix_user_id", "")
        rooms = matrix_info.get("joined_rooms", [])
        siblings = ctx_data.extra_data.get("sibling_agents", [])

        instructions = f"""
## Matrix Communication

Matrix is your **inter-agent messaging channel**. Use it to collaborate with other Agents, exchange information, coordinate tasks, or reach out to contacts you cannot talk to directly.

Your Matrix account: `{matrix_id}`

### When to Use Matrix
- You need to **contact another Agent** (ask a question, share information, coordinate work)
- Your owner asks you to **send a message** to someone
- You want to **proactively reach out** based on your current task (e.g., gather intel, request help)
- Use `matrix_search_agents` to discover agents you haven't talked to yet

{tools_section}

### DM (1-on-1) Workflow
1. Call `matrix_create_room(agent_id, invite_user_ids="@target:server", is_group=False)`
2. The room is created and the target receives an invite
3. Use `matrix_send_message` with the returned `room_id` to send messages
- Sibling agents (same owner) **auto-accept** invites instantly
- External agents accept at their own pace — your call returns immediately, do NOT wait

### Group Chat Workflow
1. Call `matrix_create_room(agent_id, invite_user_ids="@a:server,@b:server", name="Topic Name", is_group=True)`
2. All listed users receive invites; sibling agents auto-accept, others accept asynchronously
3. Use `matrix_send_message` with the returned `room_id` to send messages
4. To add more members later: `matrix_invite_to_room(agent_id, room_id, invite_user_id="@new:server")`
- Always provide a meaningful room `name` — e.g., "Project Alpha Coordination" or "Weekly Sync"

### @Mention Rules (Group Chats)
In group rooms, **only mentioned agents will be activated**. You MUST use the `mention_list` parameter in `matrix_send_message` to control who should respond:
- **Mention specific agents**: `mention_list="@alice:server,@bob:server"` — only Alice and Bob will process the message
- **Mention everyone**: `mention_list="@everyone"` — all agents in the room will be activated
- **No mention_list**: nobody is triggered (except the room creator, who always sees messages)
- In **DM rooms**, `mention_list` is not needed — the recipient is always triggered automatically

### Message Source Recognition
Every incoming message carries a **channel tag** (e.g., `[Matrix · AgentName · @id:server · !room:server]`).
- When you see `[Matrix · ...]` at the beginning of user input, it means this message came from Matrix, NOT from your owner
- Treat Matrix messages as **peer-to-peer Agent communication** — the sender is another Agent or external user
- When you see `[Direct · ...]` or no channel tag, the message is from your owner via the main chat interface

### Reply Discipline
When responding to Matrix messages:
- **Stop replying** when the conversation reaches a natural end (e.g., "好的", "谢谢", "got it", acknowledgments)
- **Do NOT ping-pong**: if you've answered the question and the other party only acknowledges, do not reply again
- **Do NOT repeat** what you already said with minor variations just to fill space
- **Silence is acceptable**: not every message needs a response. Only reply when you have substantive content
- In group rooms, you only receive messages when you are @mentioned — always reply to those
"""

        if rooms:
            instructions += "\n### Your Joined Rooms\n"
            for r in rooms[:10]:  # Cap at 10 rooms in instructions
                room_line = f"- `{r.get('room_id', '')}` — {r.get('name', 'unnamed')}"
                members = r.get("members", [])
                if members:
                    member_names = [
                        f"{m.get('agent_name', 'unknown')} (`{m.get('matrix_user_id', '')}`)"
                        for m in members
                    ]
                    room_line += f"  [Members: {', '.join(member_names)}]"
                instructions += room_line + "\n"

        # Known agents: social network first, then sibling cards (deduplicated)
        known_agents = ctx_data.extra_data.get("known_agent_entities", [])
        seen_agent_names: set[str] = set()

        if known_agents:
            instructions += "\n### Known Agents (from Social Network)\n"
            for a in known_agents[:50]:
                name = a.get("entity_name", "Unknown")
                seen_agent_names.add(name.lower())
                desc = a.get("entity_description", "")
                tags = a.get("tags", [])
                matrix_id = _extract_matrix_id(a.get("contact_info", {}))
                line = f"- **{name}**"
                if matrix_id:
                    line += f" (`{matrix_id}`)"
                if desc:
                    line += f" — {desc[:80]}"
                if tags:
                    line += f"  [{', '.join(tags[:5])}]"
                instructions += line + "\n"

        if siblings:
            # Filter out siblings already covered by social network
            unseen = [
                s for s in siblings
                if s.get("name", "").lower() not in seen_agent_names
            ]
            if unseen:
                instructions += "\n### Sibling Agents (same owner)\n"
                for s in unseen[:50]:
                    name = s.get("name", "Unknown")
                    mid = s.get("matrix", {}).get("user_id", "N/A")
                    role = s.get("role", "")
                    instructions += f"- **{name}** (`{mid}`) — {role}\n"

        return instructions

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject Matrix-specific context.

        Delegates to _matrix_hooks.matrix_hook_data_gathering.
        Only injects Matrix's own info — does NOT touch Social Network.
        """
        try:
            # Derive workspace paths from settings
            from xyz_agent_context.settings import settings
            base_workspace_path = settings.base_working_path
            workspace_path = ""
            if self.agent_id and self.user_id:
                import os
                workspace_path = os.path.join(
                    base_workspace_path, f"{self.agent_id}_{self.user_id}"
                )

            return await matrix_hook_data_gathering(
                agent_id=self.agent_id,
                db=self.db,
                ctx_data=ctx_data,
                workspace_path=workspace_path,
                base_workspace_path=base_workspace_path,
            )
        except Exception as e:
            logger.error(f"MatrixModule hook_data_gathering failed: {e}")
            return ctx_data

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        """
        Post-execution cleanup for Matrix.

        Delegates to _matrix_hooks.matrix_hook_after_event_execution.
        Only does Matrix's own cleanup — entity extraction is SocialNetwork's job.
        """
        try:
            await matrix_hook_after_event_execution(params=params, db=self.db)
        except Exception as e:
            logger.error(f"MatrixModule hook_after_event_execution failed: {e}")

    # =========================================================================
    # Channel Sender Registration
    # =========================================================================

    @classmethod
    def register_channel_sender(cls) -> None:
        """
        Register the Matrix sender in ChannelSenderRegistry.

        Called once at module init. Enables contact_agent composite operation
        to route messages through Matrix.
        """
        async def matrix_send_to_agent(
            agent_id: str,
            target_id: str,
            message: str,
            room_id: str = "",
            **kwargs,
        ) -> dict:
            """
            Send a message to a target agent via Matrix.

            If room_id is provided, sends directly. Otherwise creates a new room.

            Args:
                agent_id: Sender agent ID
                target_id: Target Matrix user ID
                message: Message content
                room_id: Existing room ID (optional)

            Returns:
                Result dict
            """
            from xyz_agent_context.utils import get_db_client
            from ._matrix_credential_manager import ensure_agent_registered

            is_group = kwargs.get("is_group", False)

            db = await get_db_client()
            cred = await ensure_agent_registered(db, agent_id)
            if not cred:
                return {"success": False, "error": "No Matrix credentials and auto-registration failed"}

            client = NexusMatrixClient(server_url=cred.server_url)
            try:
                # Create room if needed
                if not room_id:
                    result = await client.create_room(
                        api_key=cred.api_key,
                        invite_user_ids=[target_id],
                        is_direct=(not is_group),
                    )
                    if not result:
                        return {"success": False, "error": "Failed to create room"}
                    room_id = result.get("room_id", "")

                # Send message
                send_result = await client.send_message(
                    api_key=cred.api_key,
                    room_id=room_id,
                    content=message,
                )

                return {
                    "success": bool(send_result),
                    "room_id": room_id,
                    "data": send_result,
                }
            finally:
                await client.close()

        ChannelSenderRegistry.register("matrix", matrix_send_to_agent)
