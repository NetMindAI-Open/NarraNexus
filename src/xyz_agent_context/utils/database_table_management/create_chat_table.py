#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create inbox_table table

Contains InboxTableManager definition. InboxMessage model is imported from schema.inbox_schema.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_chat_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_chat_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict

try:
    from xyz_agent_context.schema.inbox_schema import InboxMessage
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        create_table_interactive,
    )
    from xyz_agent_context.utils.db_factory import get_db_client
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.schema.inbox_schema import InboxMessage
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        create_table_interactive,
    )
    from xyz_agent_context.utils.db_factory import get_db_client


# ===== Inbox Table Manager =====

class InboxTableManager(BaseTableManager):
    """
    Inbox Table Manager

    Manages the inbox_table table, Inbox is the data store for ChatModule's messaging capability.
    """
    model = InboxMessage
    table_name = "inbox_table"
    field_name_mapping = {"id": "id"}
    ignored_fields = set()  # Do not ignore any fields, created_at needs to be inserted
    protected_columns = {"id", "created_at"}
    new_column_defaults: Dict[str, str] = {}

    unique_id_field = "message_id"
    json_fields = {"source"}

    # Fields that should use TEXT instead of VARCHAR(255)
    _text_fields = {"content"}

    @classmethod
    def get_mysql_type(cls, field_name: str, field_type: type, field_info: Any) -> str:
        """Override: use TEXT for content field (messages can be long)."""
        if field_name in cls._text_fields and (field_type == str or field_type == "str"):
            return "TEXT NOT NULL"
        return super().get_mysql_type(field_name, field_type, field_info)

    @classmethod
    async def sync_table(cls, dry_run: bool = False) -> None:
        """
        Override: fix column type issues before standard column sync.

        Handles:
        1. message_type ENUM — ensure 'channel_message' is included
        2. content column — ensure TEXT (not VARCHAR)
        3. Standard column add/drop via parent sync_table
        """
        db = await get_db_client()

        # Fix message_type ENUM
        rows = await db.execute(
            "SHOW COLUMNS FROM inbox_table WHERE Field='message_type'",
            fetch=True,
        )
        if rows and "channel_message" not in rows[0].get("Type", ""):
            sql = (
                "ALTER TABLE inbox_table MODIFY COLUMN message_type "
                "ENUM('job_result','system','agent','channel_message') NOT NULL"
            )
            if dry_run:
                print(f"  [DRY RUN] {sql}")
            else:
                await db.execute(sql, fetch=False)
                print("  [DONE] Added 'channel_message' to message_type ENUM")

        # Fix content column type
        rows = await db.execute(
            "SHOW COLUMNS FROM inbox_table WHERE Field='content'",
            fetch=True,
        )
        if rows and rows[0].get("Type", "").lower() != "text":
            sql = "ALTER TABLE inbox_table MODIFY COLUMN content TEXT NOT NULL"
            if dry_run:
                print(f"  [DRY RUN] {sql}")
            else:
                await db.execute(sql, fetch=False)
                print("  [DONE] Changed content to TEXT")

        # Standard column add/drop sync
        await super().sync_table(dry_run=dry_run)


# ===== Index Definitions =====

INDEXES = [
    ("idx_user_id", ["user_id"], False),
    ("idx_is_read", ["is_read"], False),
]


# ===== CLI =====

async def main():
    parser = argparse.ArgumentParser(description="Create inbox_table table")
    parser.add_argument("--force", "-f", action="store_true", help="Force drop existing table and recreate")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("Inbox Table Creation Tool")
    print("=" * 60)

    if args.interactive:
        await create_table_interactive(InboxTableManager, INDEXES)
    else:
        await create_table(InboxTableManager, INDEXES, force=args.force)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nOperation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
