"""
@file_name: run_discord_trigger.py
@date: 2026-06-16
@description: Standalone entry point for DiscordTrigger.

Usage:
    uv run python -m xyz_agent_context.module.discord_module.run_discord_trigger
"""

import asyncio

from loguru import logger

from xyz_agent_context.utils.logging import setup_logging


async def main():
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate
    from xyz_agent_context.module.discord_module.discord_trigger import DiscordTrigger

    db = await get_db_client()

    # Ensure tables exist (channel_discord_credentials, channel_seen_messages, ...)
    await auto_migrate(db._backend)

    trigger = DiscordTrigger(max_workers=3)
    await trigger.start(db)

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await trigger.stop()
    finally:
        # Drain loguru's enqueue=True async sinks BEFORE asyncio.run() tears
        # down the loop (same fix as run_lark_trigger / run_slack_trigger).
        flush = logger.complete()
        if hasattr(flush, "__await__"):
            await flush


if __name__ == "__main__":
    setup_logging("discord_trigger")
    logger.info("Starting Discord Trigger...")
    asyncio.run(main())
