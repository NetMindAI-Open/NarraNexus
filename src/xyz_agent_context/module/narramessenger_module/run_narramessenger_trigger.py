"""
@file_name: run_narramessenger_trigger.py
@date: 2026-06-17
@description: Standalone entry point for NarramessengerTrigger.

Usage:
    uv run python -m xyz_agent_context.module.narramessenger_module.run_narramessenger_trigger
"""

import asyncio

from loguru import logger

from xyz_agent_context.utils.logging import setup_logging


async def main():
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate
    from xyz_agent_context.module.narramessenger_module.narramessenger_trigger import (
        NarramessengerTrigger,
    )

    db = await get_db_client()

    # Ensure tables exist (channel_narramessenger_credentials, channel_seen_messages, ...)
    await auto_migrate(db._backend)

    trigger = NarramessengerTrigger(max_workers=3)
    await trigger.start(db)

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await trigger.stop()
    finally:
        flush = logger.complete()
        if hasattr(flush, "__await__"):
            await flush


if __name__ == "__main__":
    setup_logging("narramessenger_trigger")
    logger.info("Starting NarraMessenger Trigger...")
    asyncio.run(main())
