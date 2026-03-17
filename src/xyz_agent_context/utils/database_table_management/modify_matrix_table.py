#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modify matrix_credentials table — standalone schema sync script

This is an independent script for syncing table structure changes.
External scripts should NOT import anything from this file.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/modify_matrix_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/modify_matrix_table.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    from xyz_agent_context.utils.database_table_management.create_matrix_table import (
        MatrixCredentialsTableManager,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.database_table_management.create_matrix_table import (
        MatrixCredentialsTableManager,
    )


async def sync_matrix_table(dry_run: bool = False) -> None:
    """Sync matrix_credentials table structure with Pydantic model."""
    await MatrixCredentialsTableManager.sync_table(dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(description="Sync matrix_credentials table schema")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without executing")
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(sync_matrix_table(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
