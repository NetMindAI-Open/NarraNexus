#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create embeddings_store table

Stores embedding vectors for all entity types (narrative, job, entity).
Supports multi-model coexistence: each (entity_type, entity_id, model) combination
stores a separate vector, enabling lazy migration when users switch embedding models.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_embeddings_store_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_embeddings_store_table.py --force
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
CREATE TABLE IF NOT EXISTS `embeddings_store` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `entity_type` VARCHAR(32) NOT NULL COMMENT 'Entity type: narrative / job / entity',
    `entity_id` VARCHAR(64) NOT NULL COMMENT 'Entity primary key (e.g. nar_xxx, job_xxx)',
    `model` VARCHAR(128) NOT NULL COMMENT 'Embedding model ID (e.g. text-embedding-3-small)',
    `dimensions` INT UNSIGNED NOT NULL COMMENT 'Vector dimensions (e.g. 1536, 1024)',
    `vector` JSON NOT NULL COMMENT 'Embedding vector as JSON array of floats',
    `source_text` TEXT NULL COMMENT 'Original text used for embedding (for re-embedding without querying source table)',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_entity_model` (`entity_type`, `entity_id`, `model`),
    INDEX `idx_type_model` (`entity_type`, `model`),
    INDEX `idx_entity` (`entity_type`, `entity_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Multi-model embedding vector store for lazy migration';
"""

DROP_TABLE_SQL = "DROP TABLE IF EXISTS `embeddings_store`;"


# ===== Entry Point =====

async def create_embeddings_store_table(force: bool = False) -> None:
    """Create the embeddings_store table."""
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        check_table_exists,
    )
    from xyz_agent_context.utils import get_db_client

    table_name = "embeddings_store"
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
    parser = argparse.ArgumentParser(description="Create embeddings_store table")
    parser.add_argument("--force", action="store_true", help="Drop and recreate if exists")
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(create_embeddings_store_table(force=args.force))


if __name__ == "__main__":
    main()
