"""
@file_name: telegram_trigger.py
@author: NetMind.AI
@date: 2026-03-04
@description: TelegramTrigger - Multi-Bot background management service.
              Replaces services/telegram_bot.py with support for N Agents x N Bots isolation.

              Startup flow:
              1. Load all ACTIVE bindings from DB -> start Long Polling for each Bot
              2. Poll DB every 30s, diff -> dynamically add/remove Bots
              3. Backward compatibility: if .env has old TELEGRAM_BOT_TOKEN and no DB record, auto-migrate

Usage:
    uv run python -m xyz_agent_context.module.telegram_module.telegram_trigger
"""

import asyncio
import signal
from typing import Dict, Optional

from loguru import logger
from telegram import Bot, Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from xyz_agent_context.schema import AgentTextDelta, ProgressMessage
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.settings import settings


def _split_message(text: str, max_len: int = 4096):
    """Split long text into chunks, each no longer than max_len characters."""
    for i in range(0, len(text), max_len):
        yield text[i:i + max_len]


class TelegramTrigger:
    """
    Multi-Bot background management service.

    Each Bot runs as an asyncio Task (Long Polling) — I/O-bound, no CPU blocking.
    A DB sync loop polls every 30s and dynamically adds/removes Bots on diff.
    """

    def __init__(self, poll_interval: int = None):
        # running_bots: agent_id -> PTB Application
        self.running_bots: Dict[str, object] = {}
        # Per-Bot semaphore to prevent concurrent LLM call floods
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._poll_interval = poll_interval or settings.telegram_trigger_poll_interval
        self._stop_event = asyncio.Event()
        self._db = None

    async def _get_db(self):
        """Lazily initialize the DB connection."""
        if self._db is None:
            from xyz_agent_context.utils.database import AsyncDatabaseClient
            self._db = await AsyncDatabaseClient.create()
        return self._db

    # =========================================================================
    # Startup
    # =========================================================================

    async def start(self):
        """Start TelegramTrigger: migrate legacy config, load bindings, start DB sync loop."""
        logger.info("=" * 60)
        logger.info("TelegramTrigger starting...")
        logger.info(f"  DB poll interval: {self._poll_interval}s")
        logger.info("=" * 60)

        db = await self._get_db()

        # Ensure DB tables exist (auto-create on first run)
        await self._ensure_tables(db)

        # Load all ACTIVE bindings and start Bots
        from xyz_agent_context.repository.telegram_repository import TelegramBindingRepository
        repo = TelegramBindingRepository(db)
        active_bindings = await repo.get_all_active()
        for binding in active_bindings:
            await self.add_bot(binding.agent_id, binding.bot_token, binding.bot_username)

        logger.info(f"TelegramTrigger started {len(self.running_bots)} bot(s)")

        # Start DB sync loop
        await self._sync_loop()

    async def stop(self):
        """Stop all running Bots."""
        logger.info("TelegramTrigger stopping...")
        self._stop_event.set()
        for agent_id in list(self.running_bots.keys()):
            await self.remove_bot(agent_id)
        if self._db:
            await self._db.close()
        logger.info("TelegramTrigger stopped")

    # =========================================================================
    # Bot management
    # =========================================================================

    async def add_bot(
        self,
        agent_id: str,
        token: str,
        bot_username: Optional[str] = None,
    ):
        """Start Long Polling for a Bot."""
        if agent_id in self.running_bots:
            logger.debug(f"[TelegramTrigger] Bot already running: agent_id={agent_id}")
            return

        logger.info(f"[TelegramTrigger] Starting bot: agent_id={agent_id}, @{bot_username}")
        try:
            app = (
                ApplicationBuilder()
                .token(token)
                .build()
            )
            # Limit concurrent LLM calls per Bot to 5
            self._semaphores[agent_id] = asyncio.Semaphore(5)

            handler = self._make_handler(agent_id, bot_username or "")
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler))

            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)

            self.running_bots[agent_id] = app
            logger.info(f"[TelegramTrigger] ✅ Bot started: agent_id={agent_id}, @{bot_username}")
        except Exception as e:
            logger.error(f"[TelegramTrigger] ❌ Bot failed to start: agent_id={agent_id}, error={e}")

    async def remove_bot(self, agent_id: str):
        """Stop and remove a Bot."""
        app = self.running_bots.pop(agent_id, None)
        self._semaphores.pop(agent_id, None)
        if app is None:
            return

        logger.info(f"[TelegramTrigger] Stopping bot: agent_id={agent_id}")
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            logger.info(f"[TelegramTrigger] ✅ Bot stopped: agent_id={agent_id}")
        except Exception as e:
            logger.warning(f"[TelegramTrigger] Error stopping bot: agent_id={agent_id}, error={e}")

    # =========================================================================
    # DB sync loop
    # =========================================================================

    async def _sync_loop(self):
        """Poll DB every N seconds and dynamically add/remove Bots on diff."""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self._poll_interval)
                if self._stop_event.is_set():
                    break
                await self._sync_from_db()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TelegramTrigger] DB sync error: {e}")

    async def _sync_from_db(self):
        """Load ACTIVE bindings from DB and add/remove Bots based on diff."""
        try:
            db = await self._get_db()
            from xyz_agent_context.repository.telegram_repository import TelegramBindingRepository
            repo = TelegramBindingRepository(db)
            active_bindings = await repo.get_all_active()

            active_ids = {b.agent_id: b for b in active_bindings}
            running_ids = set(self.running_bots.keys())

            # Add new bots
            to_add = set(active_ids.keys()) - running_ids
            for agent_id in to_add:
                b = active_ids[agent_id]
                await self.add_bot(agent_id, b.bot_token, b.bot_username)

            # Remove disabled bots
            to_remove = running_ids - set(active_ids.keys())
            for agent_id in to_remove:
                await self.remove_bot(agent_id)

            if to_add or to_remove:
                logger.info(
                    f"[TelegramTrigger] DB sync: +{len(to_add)} -{len(to_remove)}, "
                    f"running={len(self.running_bots)}"
                )
        except Exception as e:
            logger.error(f"[TelegramTrigger] _sync_from_db error: {e}")

    # =========================================================================
    # Message handling
    # =========================================================================

    def _make_handler(self, agent_id: str, bot_username: str):
        """
        Create a message handler closure for a specific Bot.

        Prefixes user_id with "tg:" to avoid collision with Web Chat sessions.
        Upserts telegram_chat_sessions on every message so hook_data_gathering
        can look up chat_id.
        """
        async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.message or not update.message.text:
                return

            tg_user_id = str(update.message.from_user.id)
            chat_id = update.message.chat_id
            user_id = f"tg:{tg_user_id}"
            input_text = update.message.text

            logger.info(
                f"[TelegramTrigger] Message received: agent={agent_id}, "
                f"user={tg_user_id}, chat={chat_id}, text={input_text[:60]!r}"
            )

            # Send typing indicator
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

            # Bridge: upsert chat_session so hook_data_gathering can find chat_id
            try:
                db = await self._get_db()
                from xyz_agent_context.repository.telegram_repository import TelegramSessionRepository
                session_repo = TelegramSessionRepository(db)
                await session_repo.upsert_session(agent_id, tg_user_id, chat_id, bot_username)
            except Exception as e:
                logger.warning(f"[TelegramTrigger] upsert_session failed: {e}")

            # Limit concurrency via semaphore
            semaphore = self._semaphores.get(agent_id, asyncio.Semaphore(5))
            async with semaphore:
                await self._run_agent_and_reply(
                    context=context,
                    agent_id=agent_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    input_text=input_text,
                )

        return handle

    async def _run_agent_and_reply(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        agent_id: str,
        user_id: str,
        chat_id: int,
        input_text: str,
    ):
        """Call AgentRuntime, collect the response, and send it back to Telegram in chunks."""
        from xyz_agent_context.agent_runtime import AgentRuntime

        runtime = AgentRuntime()
        full_response = ""

        try:
            async for message in runtime.run(
                agent_id=agent_id,
                user_id=user_id,
                input_content=input_text,
                working_source=WorkingSource.IM,
            ):
                if isinstance(message, AgentTextDelta):
                    full_response += message.delta
                elif isinstance(message, ProgressMessage):
                    # Handle send_message_to_user_directly tool calls
                    details = message.details or {}
                    tool_name = details.get("tool_name", "")
                    if tool_name.endswith("send_message_to_user_directly"):
                        content = details.get("arguments", {}).get("content", "")
                        if content and content not in full_response:
                            full_response += content
        except Exception as e:
            logger.error(f"[TelegramTrigger] AgentRuntime error: agent={agent_id}, error={e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="Sorry, an error occurred while processing your message. Please try again later.",
            )
            return

        if full_response:
            for chunk in _split_message(full_response, 4096):
                await context.bot.send_message(chat_id=chat_id, text=chunk)
        else:
            logger.warning(f"[TelegramTrigger] AgentRuntime returned empty response: agent={agent_id}")

    # =========================================================================
    # Auto table creation
    # =========================================================================

    async def _ensure_tables(self, db) -> None:
        """Auto-create required DB tables on first run (IF NOT EXISTS, idempotent)."""
        create_bindings = """
            CREATE TABLE IF NOT EXISTS telegram_bot_bindings (
                id          BIGINT AUTO_INCREMENT PRIMARY KEY,
                agent_id    VARCHAR(64)  NOT NULL UNIQUE,
                bot_token   VARCHAR(128) NOT NULL,
                bot_username VARCHAR(64) DEFAULT NULL,
                status      ENUM('ACTIVE','DISABLED') NOT NULL DEFAULT 'ACTIVE',
                created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_telegram_status (status)
            )
        """
        create_sessions = """
            CREATE TABLE IF NOT EXISTS telegram_chat_sessions (
                id              BIGINT AUTO_INCREMENT PRIMARY KEY,
                agent_id        VARCHAR(64) NOT NULL,
                telegram_user_id VARCHAR(64) NOT NULL,
                chat_id         BIGINT NOT NULL,
                bot_username    VARCHAR(64) DEFAULT NULL,
                last_seen_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_agent_user (agent_id, telegram_user_id)
            )
        """
        try:
            await db.execute(create_bindings, fetch=False)
            await db.execute(create_sessions, fetch=False)
            logger.debug("[TelegramTrigger] DB tables ready")
        except Exception as e:
            logger.error(f"[TelegramTrigger] Failed to create tables: {e}")
            raise


# =========================================================================
# Entry point
# =========================================================================

async def main():
    trigger = TelegramTrigger()

    # Register SIGINT / SIGTERM signal handlers
    loop = asyncio.get_running_loop()

    def _handle_signal():
        logger.info("[TelegramTrigger] Stop signal received")
        asyncio.create_task(trigger.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    await trigger.start()


if __name__ == "__main__":
    asyncio.run(main())
