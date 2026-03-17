#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create cost_records table

Stores per-call LLM API cost records for tracking token usage and spend.
Used by cost_tracker.py for persistence and by the cost API endpoint for queries.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_cost_records_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_cost_records_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project imports work when running as standalone script
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root / "src"))


# ===== Table Creation SQL =====

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `cost_records` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `agent_id` VARCHAR(64) NOT NULL COMMENT 'Agent that incurred the cost',
    `event_id` VARCHAR(64) NULL COMMENT 'Associated event (nullable for standalone calls)',
    `call_type` VARCHAR(32) NOT NULL COMMENT 'agent_loop / llm_function / embedding',
    `model` VARCHAR(128) NOT NULL COMMENT 'Model identifier',
    `input_tokens` INT NOT NULL DEFAULT 0,
    `output_tokens` INT NOT NULL DEFAULT 0,
    `total_cost_usd` DECIMAL(10, 6) NOT NULL DEFAULT 0,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    INDEX `idx_agent_id` (`agent_id`),
    INDEX `idx_created_at` (`created_at`),
    INDEX `idx_call_type` (`call_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='LLM API call cost records';
"""

DROP_TABLE_SQL = "DROP TABLE IF EXISTS `cost_records`;"


# ===== Entry Point =====

async def create_cost_records_table(force: bool = False) -> None:
    """Create the cost_records table using raw SQL (no TableManager needed)."""
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        check_table_exists,
    )
    from xyz_agent_context.utils import get_db_client

    table_name = "cost_records"
    print(f"\n{'='*60}")
    print(f"Creating table: {table_name}")
    print(f"{'='*60}")

    exists = await check_table_exists(table_name)

    if exists and not force:
        print(f"\nTable `{table_name}` already exists, no need to create.")
        return

    db = await get_db_client()

    if exists and force:
        print(f"Dropping existing table `{table_name}`...")
        await db.execute(DROP_TABLE_SQL)

    print(f"Creating table `{table_name}`...")
    await db.execute(CREATE_TABLE_SQL)
    print(f"✅ Table `{table_name}` created successfully.")


def main():
    parser = argparse.ArgumentParser(description="Create cost_records table")
    parser.add_argument("--force", action="store_true", help="Drop and recreate if exists")
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(create_cost_records_table(force=args.force))


if __name__ == "__main__":
    main()
