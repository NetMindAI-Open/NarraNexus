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


if __name__ == "__main__":
    setup_logging("lark_trigger")
    logger.info("Starting Lark Trigger...")
    try:
        asyncio.run(main())
    finally:
        # Flush enqueue=True records so the last shutdown lines survive.
        asyncio.run(logger.complete())
