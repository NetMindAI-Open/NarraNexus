#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modify inbox_table — standalone schema sync script

This is an independent script for syncing table structure changes.
External scripts should NOT import anything from this file.

Handles:
1. Add 'channel_message' to message_type ENUM (if missing)
2. Change content column from VARCHAR(255) to TEXT (if needed)
3. Sync any new/removed columns via BaseTableManager.sync_table

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/modify_inbox_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/modify_inbox_table.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    from xyz_agent_context.utils.database_table_management.create_chat_table import (
        InboxTableManager,
    )
    from xyz_agent_context.utils.db_factory import get_db_client
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.database_table_management.create_chat_table import (
        InboxTableManager,
    )
    from xyz_agent_context.utils.db_factory import get_db_client


async def _fix_message_type_enum(db_client, dry_run: bool) -> None:
    """Ensure message_type ENUM includes 'channel_message'."""
    rows = await db_client.execute(
        "SHOW COLUMNS FROM inbox_table WHERE Field='message_type'",
        fetch=True,
    )
    if not rows:
        return

    col_type = rows[0].get("Type", "")
    if "channel_message" in col_type:
        print("  [OK] message_type ENUM already includes 'channel_message'")
        return

    sql = (
        "ALTER TABLE inbox_table MODIFY COLUMN message_type "
        "ENUM('job_result','system','agent','channel_message') NOT NULL"
    )
    if dry_run:
        print(f"  [DRY RUN] Would execute: {sql}")
    else:
        await db_client.execute(sql, fetch=False)
        print("  [DONE] Added 'channel_message' to message_type ENUM")


async def _fix_content_column(db_client, dry_run: bool) -> None:
    """Ensure content column is TEXT (not VARCHAR)."""
    rows = await db_client.execute(
        "SHOW COLUMNS FROM inbox_table WHERE Field='content'",
        fetch=True,
    )
    if not rows:
        return

    col_type = rows[0].get("Type", "").lower()
    if col_type == "text":
        print("  [OK] content column is already TEXT")
        return

    sql = "ALTER TABLE inbox_table MODIFY COLUMN content TEXT NOT NULL"
    if dry_run:
        print(f"  [DRY RUN] Would execute: {sql}")
    else:
        await db_client.execute(sql, fetch=False)
        print(f"  [DONE] Changed content column from {col_type} to TEXT")


async def sync_inbox_table(dry_run: bool = False) -> None:
    """Sync inbox_table structure with Pydantic model."""
    print("\n" + "=" * 60)
    print("Inbox Table Schema Sync")
    print("=" * 60)

    db_client = await get_db_client()

    # 1. Fix ENUM and column type issues first
    print("\n--- Column type fixes ---")
    await _fix_message_type_enum(db_client, dry_run)
    await _fix_content_column(db_client, dry_run)

    # 2. Run standard column add/drop sync
    print("\n--- Column add/drop sync ---")
    await InboxTableManager.sync_table(dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(description="Sync inbox_table schema")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without executing")
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(sync_inbox_table(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
