"""
@file_name: telegram_module.py
@author: NetMind.AI
@date: 2026-03-04
@description: TelegramModule - Standard XYZBaseModule subclass.
              Provides Telegram Bot binding management and IM channel context injection.

MCP Port: 7806
MCP Tools:
    - register_telegram_bot(agent_id, bot_token)
    - unregister_telegram_bot(agent_id)
    - send_telegram_message(agent_id, chat_id, content)
    - get_telegram_binding_info(agent_id)
"""

from typing import Any, Optional, List

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
)
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.utils import DatabaseClient


class TelegramModule(XYZBaseModule):
    """
    Telegram IM channel module.

    Capabilities:
    1. Lets the Agent register/unregister Bots and send messages via MCP tools
    2. hook_data_gathering injects chat_id / bot_username for Telegram users
    3. get_instructions injects Telegram channel guidance (character limits, etc.)

    Works together with TelegramTrigger:
    - TelegramTrigger: receives messages and calls AgentRuntime
    - TelegramModule: gives the Agent IM channel awareness and tools
    """

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None,
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.port = 7806

        self.instructions = """
## Telegram Module

### Current Channel
{im_context}

### Capabilities
- **Register Bot**: Call register_telegram_bot when the user provides a token
- **Unregister Bot**: Call unregister_telegram_bot to stop polling
- **Send message**: Call send_telegram_message to proactively send a message to the user
- **Check binding**: Call get_telegram_binding_info to view current binding status

### IMPORTANT — Sending notifications to Telegram users
If the current user is a Telegram user (im_context shows a chat_id), you MUST use
`send_telegram_message` to deliver any notification or reminder. Do NOT use
`agent_send_content_to_user_inbox` as the sole delivery method — the user will NOT
see inbox messages on Telegram. Always call `send_telegram_message(agent_id, chat_id, content)`
using the chat_id shown above.

### Notes
- Telegram message limit is 4096 characters; messages are split automatically
- After registration, the Bot starts receiving messages within 30 seconds
"""

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="TelegramModule",
            priority=3,
            enabled=True,
            description=(
                "Manages Telegram Bot bindings and message delivery. "
                "Activate when: the user asks to send a Telegram message or notification, "
                "register/unregister a Telegram Bot, check binding status, "
                "or when a job/reminder needs to notify the user via Telegram."
            ),
            module_type="task",
        )

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject Telegram context when the current user is a Telegram user (user_id starts with "tg:").
        Works across all working_source values (IM / JOB / CHAT) so that Job-triggered
        executions targeting a Telegram user also get chat_id injected.
        """
        raw_user_id = ctx_data.user_id or ""
        if not raw_user_id.startswith("tg:"):
            # Not a Telegram user — inject empty placeholder so instructions format without error
            ctx_data.extra_data["im_context"] = "Not a Telegram channel"
            return ctx_data

        # Strip "tg:" prefix to get the bare Telegram user ID
        telegram_user_id = raw_user_id.removeprefix("tg:")

        try:
            from xyz_agent_context.repository.telegram_repository import TelegramSessionRepository
            session_repo = TelegramSessionRepository(self.db)
            session = await session_repo.get_session(self.agent_id, telegram_user_id)

            if session:
                ctx_data.extra_data["im_channel"] = "telegram"
                ctx_data.extra_data["im_chat_id"] = session.chat_id
                ctx_data.extra_data["im_bot_username"] = session.bot_username or ""
                ctx_data.extra_data["im_context"] = (
                    f"Channel: Telegram | Bot: @{session.bot_username or 'unknown'} | "
                    f"chat_id: {session.chat_id}"
                )
                logger.debug(
                    f"[TelegramModule] Injected IM context: chat_id={session.chat_id}, "
                    f"bot=@{session.bot_username}"
                )
            else:
                ctx_data.extra_data["im_context"] = "Channel: Telegram (session not found — user has not sent a message yet)"

        except Exception as e:
            logger.warning(f"[TelegramModule] hook_data_gathering failed: {e}")
            ctx_data.extra_data["im_context"] = "Channel: Telegram"

        return ctx_data

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """Inject Telegram channel instructions into the system prompt."""
        im_context = ctx_data.extra_data.get("im_context", "")
        return self.instructions.format(im_context=im_context)

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        """Lightweight post-processing: update last_seen_at (message delivery is handled by TelegramTrigger)."""
        if params.working_source != WorkingSource.IM:
            return

        raw_user_id = params.user_id or ""
        telegram_user_id = raw_user_id.removeprefix("tg:")
        if not telegram_user_id:
            return

        try:
            from xyz_agent_context.repository.telegram_repository import TelegramSessionRepository
            session_repo = TelegramSessionRepository(self.db)
            # upsert automatically updates last_seen_at via ON DUPLICATE KEY UPDATE ... last_seen_at = NOW()
            existing = await session_repo.get_session(self.agent_id, telegram_user_id)
            if existing:
                await session_repo.upsert_session(
                    agent_id=self.agent_id,
                    telegram_user_id=telegram_user_id,
                    chat_id=existing.chat_id,
                    bot_username=existing.bot_username,
                )
        except Exception as e:
            logger.warning(f"[TelegramModule] hook_after_event_execution failed to update last_seen: {e}")

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="telegram_module",
            server_url=f"http://127.0.0.1:{self.port}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        """Create FastMCP server with 4 registered tools."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("telegram_module")
        mcp.settings.port = self.port

        # -----------------------------------------------------------------
        # Tool: register_telegram_bot
        # -----------------------------------------------------------------
        @mcp.tool()
        async def register_telegram_bot(agent_id: str, bot_token: str) -> str:
            """
            Register a Telegram Bot: verify the token, write to DB, and start polling within 30s.

            Args:
                agent_id: Agent ID (current agent)
                bot_token: Telegram Bot Token (format: 123456:ABC...)

            Returns:
                Registration result description
            """
            try:
                db = await TelegramModule.get_mcp_db_client()
                from xyz_agent_context.module.telegram_module.telegram_service import TelegramService
                svc = TelegramService(db)
                binding = await svc.register_bot(agent_id, bot_token)
                return (
                    f"✅ Bot registered successfully!\n"
                    f"- Bot username: @{binding.bot_username}\n"
                    f"- Agent ID: {agent_id}\n"
                    f"- Status: {binding.status}\n"
                    f"TelegramTrigger will start polling within 30 seconds."
                )
            except ValueError as e:
                return f"❌ Registration failed: {e}"
            except Exception as e:
                logger.error(f"[TelegramModule] register_telegram_bot error: {e}")
                return f"❌ Internal error during registration: {e}"

        # -----------------------------------------------------------------
        # Tool: unregister_telegram_bot
        # -----------------------------------------------------------------
        @mcp.tool()
        async def unregister_telegram_bot(agent_id: str) -> str:
            """
            Unregister a Telegram Bot: stop polling and set binding status to DISABLED.

            Args:
                agent_id: Agent ID

            Returns:
                Unregistration result description
            """
            try:
                db = await TelegramModule.get_mcp_db_client()
                from xyz_agent_context.module.telegram_module.telegram_service import TelegramService
                svc = TelegramService(db)
                await svc.unregister_bot(agent_id)
                return f"✅ Bot unregistered for agent_id={agent_id}. TelegramTrigger will stop polling within 30 seconds."
            except Exception as e:
                logger.error(f"[TelegramModule] unregister_telegram_bot error: {e}")
                return f"❌ Error during unregistration: {e}"

        # -----------------------------------------------------------------
        # Tool: send_telegram_message
        # -----------------------------------------------------------------
        @mcp.tool()
        async def send_telegram_message(agent_id: str, chat_id: int, content: str) -> str:
            """
            Proactively send a message to a Telegram chat (Agent-initiated push, no prior user message required).
            Messages exceeding 4096 characters are split and sent automatically.

            Args:
                agent_id: Agent ID (used to look up bot_token)
                chat_id: Telegram chat_id
                content: Message content

            Returns:
                Send result description
            """
            try:
                db = await TelegramModule.get_mcp_db_client()
                from xyz_agent_context.repository.telegram_repository import TelegramBindingRepository
                repo = TelegramBindingRepository(db)
                binding = await repo.get_by_agent_id(agent_id)

                if not binding or binding.status != "ACTIVE":
                    return f"❌ No active Telegram Bot binding found for agent_id={agent_id}"

                from telegram import Bot
                bot = Bot(token=binding.bot_token)

                max_len = 4096
                chunks = [content[i:i + max_len] for i in range(0, len(content), max_len)]
                for chunk in chunks:
                    await bot.send_message(chat_id=chat_id, text=chunk)

                return f"✅ Message sent to chat_id={chat_id} ({len(chunks)} part(s))"

            except Exception as e:
                logger.error(f"[TelegramModule] send_telegram_message error: {e}")
                return f"❌ Failed to send message: {e}"

        # -----------------------------------------------------------------
        # Tool: get_telegram_binding_info
        # -----------------------------------------------------------------
        @mcp.tool()
        async def get_telegram_binding_info(agent_id: str) -> str:
            """
            Query current Telegram Bot binding status.

            Args:
                agent_id: Agent ID

            Returns:
                Binding info description
            """
            try:
                db = await TelegramModule.get_mcp_db_client()
                from xyz_agent_context.repository.telegram_repository import TelegramBindingRepository
                repo = TelegramBindingRepository(db)
                binding = await repo.get_by_agent_id(agent_id)

                if not binding:
                    return f"No Telegram Bot binding found for agent_id={agent_id}."

                return (
                    f"Telegram Bot binding info:\n"
                    f"- Agent ID: {binding.agent_id}\n"
                    f"- Bot username: @{binding.bot_username or 'unknown'}\n"
                    f"- Status: {binding.status}\n"
                    f"- Created at: {binding.created_at}\n"
                    f"- Updated at: {binding.updated_at}"
                )

            except Exception as e:
                logger.error(f"[TelegramModule] get_telegram_binding_info error: {e}")
                return f"❌ Query failed: {e}"

        return mcp
