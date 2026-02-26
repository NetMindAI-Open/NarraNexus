"""
@file_name: telegram_bot.py
@author: NexusMind
@date: 2026-02-26
@description: Telegram Bot service that forwards messages to AgentRuntime and returns replies.
              Runs as a standalone background process (Long Polling mode).

Usage:
    uv run python -m xyz_agent_context.services.telegram_bot
"""

from loguru import logger
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from xyz_agent_context.schema import AgentTextDelta, ProgressMessage, WorkingSource
from xyz_agent_context.settings import settings


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages, invoke AgentRuntime, and send the reply back to Telegram."""
    # Ignore updates without a message body or text
    if not update.message or not update.message.text:
        return

    user_id = str(update.message.from_user.id)
    input_content = update.message.text
    chat_id = update.message.chat_id

    logger.info(f"[Telegram] user={user_id} chat={chat_id} text={input_content[:80]!r}")

    # Send "typing..." indicator to improve user experience
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Lazy import to avoid circular dependencies
    from xyz_agent_context.agent_runtime import AgentRuntime

    runtime = AgentRuntime()
    full_response = ""

    try:
        async for message in runtime.run(
            agent_id=settings.telegram_agent_id,
            user_id=user_id,
            input_content=input_content,
            working_source=WorkingSource.CHAT,
        ):
            if isinstance(message, AgentTextDelta):
                full_response += message.delta
            elif isinstance(message, ProgressMessage):
                # Agent sends replies via the send_message_to_user_directly tool.
                # tool_call_item is converted to ProgressMessage; content is in details.arguments.content
                details = message.details or {}
                tool_name = details.get("tool_name", "")
                if tool_name.endswith("send_message_to_user_directly"):
                    content = details.get("arguments", {}).get("content", "")
                    if content and content not in full_response:
                        full_response += content
    except Exception as e:
        logger.error(f"[Telegram] AgentRuntime error: {e}")
        await update.message.reply_text("Sorry, an error occurred while processing your message. Please try again later.")
        return

    if full_response:
        await update.message.reply_text(full_response)
    else:
        logger.warning(f"[Telegram] AgentRuntime returned no text, user={user_id}")


def main() -> None:
    """Start the Telegram Bot (Long Polling mode)."""
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not configured, Telegram Bot cannot start")
        raise SystemExit(1)

    if not settings.telegram_agent_id:
        logger.error("TELEGRAM_AGENT_ID is not configured, Telegram Bot cannot start")
        raise SystemExit(1)

    logger.info("=" * 60)
    logger.info("Starting Telegram Bot (Long Polling mode)...")
    logger.info(f"  Agent ID: {settings.telegram_agent_id}")
    logger.info("=" * 60)

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # Only handle plain text messages, excluding commands (e.g. /start)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Telegram Bot is ready, waiting for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
