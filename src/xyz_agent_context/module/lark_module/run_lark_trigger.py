"""
@file_name: run_lark_trigger.py
@date: 2026-04-11
@description: Standalone entry point for LarkTrigger.

Usage:
    uv run python -m xyz_agent_context.module.lark_module.run_lark_trigger
"""

import asyncio

from loguru import logger

from xyz_agent_context.utils.logging import setup_logging


async def main():
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate
    from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger

    db = await get_db_client()

    # Ensure tables exist
    await auto_migrate(db._backend)

    # Migrate legacy auth_status="logged_in" → "bot_ready" (one-time, idempotent)
    from xyz_agent_context.module.lark_module._lark_credential_manager import LarkCredentialManager
    mgr = LarkCredentialManager(db)
    await mgr.migrate_legacy_auth_status()

    trigger = LarkTrigger(max_workers=3)
    await trigger.start(db)

    # Keep running until interrupted
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await trigger.stop()
    finally:
        # Drain loguru's enqueue=True async sinks BEFORE asyncio.run()
        # tears down the loop. The previous version did
        # `asyncio.run(logger.complete())` in the outer __main__ block,
        # which spun up a brand-new loop and tripped
        # `ValueError: a coroutine was expected` on shutdown — loguru's
        # AsyncSink had already been bound to the now-closed loop. By
        # keeping complete() inside the same run_forever scope we just
        # await it normally.
        flush = logger.complete()
        if hasattr(flush, "__await__"):
            await flush


if __name__ == "__main__":
    setup_logging("lark_trigger")
    logger.info("Starting Lark Trigger...")
    asyncio.run(main())
