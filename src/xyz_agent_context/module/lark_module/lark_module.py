"""
@file_name: lark_module.py
@date: 2026-04-10
@description: LarkModule — Lark/Feishu integration module.

Provides MCP tools for messaging, contacts, docs, calendar, and tasks.
Each agent can bind its own Lark bot via CLI --profile isolation.

Instance level: Agent-level (one per Agent, enabled when bot is bound).
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
)

from ._lark_credential_manager import LarkCredentialManager
from .lark_cli_client import LarkCLIClient


# MCP server port — must not conflict with other modules
# MessageBusModule: 7820, JobModule: 7803
LARK_MCP_PORT = 7830

# Shared CLI client (stateless)
_cli = LarkCLIClient()


async def _lark_send_to_agent(
    agent_id: str, target_id: str, message: str, **kwargs
) -> dict:
    """
    Channel sender function registered in ChannelSenderRegistry.
    Allows other modules to send Lark messages on behalf of an agent.
    """
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(agent_id)
    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}
    return await _cli.send_message(cred.profile_name, user_id=target_id, text=message)


class LarkModule(XYZBaseModule):
    """
    Lark/Feishu integration module.

    Enables agents to interact with Lark: search contacts, send messages,
    create documents, manage calendar events, and handle tasks.
    """

    _sender_registered = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not LarkModule._sender_registered:
            ChannelSenderRegistry.register("lark", _lark_send_to_agent)
            LarkModule._sender_registered = True

    # =========================================================================
    # Configuration
    # =========================================================================

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="LarkModule",
            priority=6,
            enabled=True,
            description=(
                "Lark/Feishu integration: search colleagues, send messages, "
                "create documents, manage calendar, and handle tasks."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="lark_module",
            server_url=f"http://localhost:{LARK_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        try:
            # Use the official MCP SDK's FastMCP (mcp.server.fastmcp) — NOT the
            # standalone `fastmcp` v2 package. The standalone package has a
            # different Settings API that breaks ModuleRunner's host/port/
            # transport_security configuration (see MessageBusModule notes).
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("lark_module")
            mcp.settings.port = LARK_MCP_PORT

            from ._lark_mcp_tools import register_lark_mcp_tools
            register_lark_mcp_tools(mcp)

            logger.info(f"LarkModule MCP server created on port {LARK_MCP_PORT}")
            return mcp
        except Exception as e:
            logger.error(f"Failed to create LarkModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """Dynamic instructions based on whether a Lark bot is bound."""
        lark_info = ctx_data.extra_data.get("lark_info")

        if not lark_info:
            return (
                "## Lark/Feishu Integration\n\n"
                "No Lark bot is bound to this agent. To enable Lark features, "
                "use the `lark_bind_bot` tool with an App ID and Secret from the "
                "Feishu/Lark Open Platform."
            )

        brand_display = "Feishu" if lark_info.get("brand") == "feishu" else "Lark"
        bot_name = lark_info.get("bot_name", "Unknown Bot")
        auth = lark_info.get("auth_status", "not_logged_in")

        if auth != "logged_in":
            return (
                f"## Lark/Feishu Integration\n\n"
                f"Bot **{bot_name}** ({brand_display}) is bound but not logged in. "
                f"Use `lark_auth_login` to complete OAuth authorization."
            )

        owner_section = ""
        owner_id = lark_info.get("owner_open_id", "")
        owner_name = lark_info.get("owner_name", "")
        if owner_id:
            owner_section = (
                f"\n**Owner identity**: {owner_name} (open_id: `{owner_id}`)\n"
                f"When the user says \"me\", \"my\", \"I\" in the context of Lark, "
                f"it refers to this person (open_id: `{owner_id}`).\n"
            )

        return (
            f"## Lark/Feishu Integration\n\n"
            f"Connected as **{bot_name}** ({brand_display}).\n"
            f"{owner_section}\n"
            f"### Always available (Bot identity):\n"
            f"- **lark_search_contacts**: Search by email or phone (name search needs OAuth)\n"
            f"- **lark_get_user_info**: Get user profile details\n"
            f"- **lark_send_message**: Send messages to chats or users\n"
            f"- **lark_reply_message**: Reply to a specific message\n"
            f"- **lark_create_chat**: Create group chats\n\n"
            f"### Require app permissions (admin must enable in Lark Open Platform):\n"
            f"- **lark_list_chat_messages**: Needs `im:message:readonly`\n"
            f"- **lark_search_chat**: Needs `im:chat:readonly`\n"
            f"- **lark_create_document / lark_fetch_document / lark_update_document**: Needs `docx:document`\n"
            f"- **lark_search_documents**: Needs `docx:document:readonly`\n"
            f"- **lark_get_agenda**: Needs `calendar:calendar.event:read`\n"
            f"- **lark_create_event**: Needs `calendar:calendar.event:create`\n"
            f"- **lark_check_freebusy**: Needs `calendar:calendar.free_busy:read`\n"
            f"- **lark_create_task**: Needs `task:task:write`\n\n"
            f"### Require user OAuth login:\n"
            f"- **lark_get_my_tasks**: Only works with user identity\n"
            f"- **lark_search_messages**: Only works with user identity\n"
            f"- **lark_search_contacts** (by name): Only works with user identity\n\n"
            f"**CRITICAL: Do NOT call lark_auth_login or lark_auth_status.** These tools do not exist.\n"
            f"OAuth login is managed by the user via the frontend settings, not by the Agent.\n"
            f"If a tool returns a permission error, simply tell the user which permission "
            f"needs to be enabled in the Lark Open Platform admin console.\n\n"
            f"**IMPORTANT Lark reply rules:**\n"
            f"- When replying to a Lark message, call `lark_send_message` **exactly ONCE**.\n"
            f"- Do NOT send multiple messages for the same reply.\n"
            f"- Do NOT reply to simple acknowledgments like 'ok', 'thanks', 'got it'.\n"
            f"- Keep replies concise and direct.\n"
            f"- Use `text` parameter (plain text), NOT `markdown`.\n"
            f"- For lists, use simple bullet points with emoji, not tables.\n\n"
            f"Use the lark_* tools to interact with {brand_display}."
        )

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """Inject Lark bot info into context if bound."""
        try:
            mgr = LarkCredentialManager(self.db)
            cred = await mgr.get_credential(self.agent_id)
            if cred and cred.is_active:
                lark_info = {
                    "app_id": cred.app_id,
                    "brand": cred.brand,
                    "bot_name": cred.bot_name,
                    "auth_status": cred.auth_status,
                    "profile_name": cred.profile_name,
                }
                if cred.owner_open_id:
                    lark_info["owner_open_id"] = cred.owner_open_id
                    lark_info["owner_name"] = cred.owner_name
                ctx_data.extra_data["lark_info"] = lark_info
        except Exception as e:
            logger.warning(f"LarkModule hook_data_gathering failed: {e}")
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        """Post-execution cleanup for Lark-triggered executions."""
        # Only process Lark-triggered executions
        ws = params.execution_ctx.working_source
        # working_source can be either the enum or its string value
        if str(ws) != WorkingSource.LARK.value:
            return
        # Future: mark messages as read, update sync state, etc.
        logger.debug(f"LarkModule after_execution for agent {params.execution_ctx.agent_id}")
