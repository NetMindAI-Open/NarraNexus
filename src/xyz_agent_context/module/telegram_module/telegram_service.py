"""
@file_name: telegram_service.py
@author: NetMind.AI
@date: 2026-03-04
@description: TelegramService - Service layer for Bot registration, unregistration, and queries.
              Called by MCP tools and TelegramTrigger; not imported by other application code.
"""

from typing import Optional

import httpx
from loguru import logger

from xyz_agent_context.repository.telegram_repository import (
    TelegramBindingRepository,
    TelegramBotBinding,
)


class TelegramService:
    """
    Telegram Bot registration, unregistration, and query service.

    Usage:
        db = await AsyncDatabaseClient.create()
        svc = TelegramService(db)
        binding = await svc.register_bot("agent_123", "123456:ABC...")
    """

    def __init__(self, db_client):
        self._repo = TelegramBindingRepository(db_client)

    async def register_bot(
        self,
        agent_id: str,
        bot_token: str,
    ) -> TelegramBotBinding:
        """
        Verify token and write binding to DB.

        1. Call Telegram getMe() API to verify the token
        2. Retrieve bot_username
        3. Upsert telegram_bot_bindings record

        Args:
            agent_id: Agent ID
            bot_token: Telegram Bot Token

        Returns:
            TelegramBotBinding record

        Raises:
            ValueError: Invalid token or API request failed
        """
        logger.info(f"[TelegramService] Registering bot: agent_id={agent_id}")

        # Verify token via Telegram getMe()
        bot_username = await self._verify_token(bot_token)

        # Write to DB
        binding = await self._repo.create_binding(
            agent_id=agent_id,
            bot_token=bot_token,
            bot_username=bot_username,
        )

        logger.info(f"[TelegramService] Bot registered successfully: @{bot_username} -> agent_id={agent_id}")
        return binding

    async def unregister_bot(self, agent_id: str) -> None:
        """
        Unregister Bot (set status to DISABLED).

        Args:
            agent_id: Agent ID
        """
        logger.info(f"[TelegramService] Unregistering bot: agent_id={agent_id}")
        await self._repo.update_status(agent_id, "DISABLED")

    async def get_binding_info(self, agent_id: str) -> Optional[TelegramBotBinding]:
        """
        Query current binding info.

        Args:
            agent_id: Agent ID

        Returns:
            TelegramBotBinding or None
        """
        return await self._repo.get_by_agent_id(agent_id)

    @staticmethod
    async def _verify_token(bot_token: str) -> str:
        """
        Call Telegram Bot API getMe() to verify the token and return bot_username.

        Args:
            bot_token: Telegram Bot Token

        Returns:
            bot_username (without @)

        Raises:
            ValueError: Invalid token
        """
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                data = resp.json()
        except Exception as e:
            raise ValueError(f"Failed to connect to Telegram API: {e}")

        if not data.get("ok"):
            description = data.get("description", "Unknown error")
            raise ValueError(f"Invalid token: {description}")

        username = data["result"].get("username", "")
        if not username:
            raise ValueError("getMe() did not return a username")

        return username
