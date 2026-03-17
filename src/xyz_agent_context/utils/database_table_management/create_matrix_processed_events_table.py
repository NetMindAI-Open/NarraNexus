#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create matrix_processed_events table

Persistent dedup layer for MatrixTrigger.
Stores event IDs that have been processed by each agent,
so that trigger restarts don't cause re-processing.

Kept lightweight: simple (event_id, agent_id) composite key
with TTL cleanup (7 days default).

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_matrix_processed_events_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_matrix_processed_events_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
    )


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `matrix_processed_events` (
    `event_id` VARCHAR(255) NOT NULL COMMENT 'Matrix event ID (e.g. $abc123)',
    `agent_id` VARCHAR(64) NOT NULL COMMENT 'Agent that processed this event',
    `processed_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`event_id`, `agent_id`),
    INDEX `idx_processed_at` (`processed_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Persistent dedup for MatrixTrigger — survives restarts';
"""


async def create_matrix_processed_events_table(force: bool = False) -> None:
    """Create the matrix_processed_events table."""
    await create_table(
        table_name="matrix_processed_events",
        create_sql=CREATE_TABLE_SQL,
        force=force,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Create matrix_processed_events table"
    )
    parser.add_argument(
        "--force", action="store_true", help="Drop and recreate if exists"
    )
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(create_matrix_processed_events_table(force=args.force))


if __name__ == "__main__":
    main()
