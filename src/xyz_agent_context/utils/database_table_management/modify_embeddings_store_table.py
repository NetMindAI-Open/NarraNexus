#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modify embeddings_store — standalone schema sync script

This is an independent script for syncing table structure changes.
External scripts should NOT import anything from this file.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/modify_embeddings_store_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/modify_embeddings_store_table.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    from xyz_agent_context.utils.db_factory import get_db_client
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.db_factory import get_db_client


async def modify_embeddings_store(dry_run: bool = False) -> None:
    """Sync embeddings_store table schema.

    Currently a placeholder — no modifications needed beyond the initial creation.
    Add migration steps here when the schema evolves.
    """
    db = await get_db_client()

    # Check if table exists
    rows = await db.execute(
        "SELECT COUNT(*) as cnt FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = 'embeddings_store'",
        fetch=True,
    )
    if not rows or rows[0]["cnt"] == 0:
        print("  [SKIP] Table embeddings_store does not exist. Run create script first.")
        return

    print("  [OK] embeddings_store table exists, no schema modifications needed.")

    # === Future migration steps go here ===
    # Example:
    # await _add_column_if_missing(db, "embeddings_store", "provider", "VARCHAR(64) NULL", dry_run)


def main():
    parser = argparse.ArgumentParser(description="Modify embeddings_store table")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401

    print(f"\n{'='*60}")
    print("Syncing table: embeddings_store")
    print(f"{'='*60}")
    asyncio.run(modify_embeddings_store(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
