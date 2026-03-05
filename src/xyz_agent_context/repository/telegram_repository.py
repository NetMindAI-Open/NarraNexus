"""
@file_name: telegram_repository.py
@author: NetMind.AI
@date: 2026-03-04
@description: Telegram Repository - telegram_bot_bindings 和 telegram_chat_sessions 的数据访问层
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from loguru import logger

from .base import BaseRepository


# ===== 实体定义 =====

@dataclass
class TelegramBotBinding:
    """telegram_bot_bindings 表实体"""
    agent_id: str
    bot_token: str
    bot_username: Optional[str] = None
    status: str = "ACTIVE"
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class TelegramChatSession:
    """telegram_chat_sessions 表实体"""
    agent_id: str
    telegram_user_id: str
    chat_id: int
    bot_username: Optional[str] = None
    id: Optional[int] = None
    last_seen_at: Optional[datetime] = None


# ===== Bindings Repository =====

class TelegramBindingRepository(BaseRepository[TelegramBotBinding]):
    """
    telegram_bot_bindings 表的数据访问层

    Usage:
        repo = TelegramBindingRepository(db_client)
        binding = await repo.get_by_agent_id("agent_123")
    """

    table_name = "telegram_bot_bindings"
    id_field = "agent_id"

    async def get_by_agent_id(self, agent_id: str) -> Optional[TelegramBotBinding]:
        """查询某 agent 的绑定记录"""
        logger.debug(f"    → TelegramBindingRepository.get_by_agent_id({agent_id})")
        return await self.find_one({"agent_id": agent_id})

    async def create_binding(
        self,
        agent_id: str,
        bot_token: str,
        bot_username: Optional[str] = None,
    ) -> TelegramBotBinding:
        """创建或更新绑定记录（upsert）"""
        logger.debug(f"    → TelegramBindingRepository.create_binding({agent_id})")
        # 使用 INSERT ... ON DUPLICATE KEY UPDATE
        query = """
            INSERT INTO telegram_bot_bindings (agent_id, bot_token, bot_username, status)
            VALUES (%s, %s, %s, 'ACTIVE')
            ON DUPLICATE KEY UPDATE
                bot_token = VALUES(bot_token),
                bot_username = VALUES(bot_username),
                status = 'ACTIVE',
                updated_at = NOW()
        """
        await self._db.execute(query, params=(agent_id, bot_token, bot_username), fetch=False)
        binding = await self.get_by_agent_id(agent_id)
        return binding

    async def update_status(self, agent_id: str, status: str) -> None:
        """更新绑定状态（ACTIVE / DISABLED）"""
        logger.debug(f"    → TelegramBindingRepository.update_status({agent_id}, {status})")
        await self._db.update(
            self.table_name,
            filters={"agent_id": agent_id},
            data={"status": status},
        )

    async def get_all_active(self) -> List[TelegramBotBinding]:
        """获取所有 ACTIVE 状态的绑定记录"""
        logger.debug("    → TelegramBindingRepository.get_all_active()")
        return await self.find(filters={"status": "ACTIVE"})

    def _row_to_entity(self, row: dict) -> TelegramBotBinding:
        return TelegramBotBinding(
            id=row.get("id"),
            agent_id=row["agent_id"],
            bot_token=row["bot_token"],
            bot_username=row.get("bot_username"),
            status=row.get("status", "ACTIVE"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: TelegramBotBinding) -> dict:
        return {
            "agent_id": entity.agent_id,
            "bot_token": entity.bot_token,
            "bot_username": entity.bot_username,
            "status": entity.status,
        }


# ===== Chat Sessions Repository =====

class TelegramSessionRepository(BaseRepository[TelegramChatSession]):
    """
    telegram_chat_sessions 表的数据访问层

    TelegramTrigger 收到消息时调用 upsert_session，
    TelegramModule.hook_data_gathering 调用 get_session 查询 chat_id。
    """

    table_name = "telegram_chat_sessions"
    id_field = "id"

    async def upsert_session(
        self,
        agent_id: str,
        telegram_user_id: str,
        chat_id: int,
        bot_username: Optional[str] = None,
    ) -> None:
        """
        收到 Telegram 消息时 upsert session 记录
        用于桥接 chat_id，让 hook_data_gathering 可以查到
        """
        logger.debug(f"    → TelegramSessionRepository.upsert_session({agent_id}, {telegram_user_id})")
        query = """
            INSERT INTO telegram_chat_sessions (agent_id, telegram_user_id, chat_id, bot_username, last_seen_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                chat_id = VALUES(chat_id),
                bot_username = VALUES(bot_username),
                last_seen_at = NOW()
        """
        await self._db.execute(
            query,
            params=(agent_id, telegram_user_id, chat_id, bot_username),
            fetch=False,
        )

    async def get_session(
        self,
        agent_id: str,
        telegram_user_id: str,
    ) -> Optional[TelegramChatSession]:
        """查询 session 记录（hook_data_gathering 用）"""
        logger.debug(f"    → TelegramSessionRepository.get_session({agent_id}, {telegram_user_id})")
        return await self.find_one({"agent_id": agent_id, "telegram_user_id": telegram_user_id})

    def _row_to_entity(self, row: dict) -> TelegramChatSession:
        return TelegramChatSession(
            id=row.get("id"),
            agent_id=row["agent_id"],
            telegram_user_id=row["telegram_user_id"],
            chat_id=row["chat_id"],
            bot_username=row.get("bot_username"),
            last_seen_at=row.get("last_seen_at"),
        )

    def _entity_to_row(self, entity: TelegramChatSession) -> dict:
        return {
            "agent_id": entity.agent_id,
            "telegram_user_id": entity.telegram_user_id,
            "chat_id": entity.chat_id,
            "bot_username": entity.bot_username,
        }
