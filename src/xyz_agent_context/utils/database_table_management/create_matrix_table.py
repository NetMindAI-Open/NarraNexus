#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create matrix_credentials table

Stores Matrix credentials and polling state for each Agent.
Used by MatrixTrigger for authentication and adaptive polling.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_matrix_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_matrix_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

try:
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        create_table_interactive,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        create_table_interactive,
    )


# ===== MatrixCredentialData Pydantic Model =====

class MatrixCredentialData(BaseModel):
    """
    Matrix Credential data model for table schema definition.

    Stores credentials and polling state — not business data.
    """
    id: Optional[int] = None
    agent_id: str = Field(..., max_length=64, description="Agent ID (primary business key)")
    nexus_agent_id: Optional[str] = Field(None, max_length=64, description="NexusMatrix internal agent ID (agt_xxx)")
    api_key: str = Field(..., max_length=255, description="NexusMatrix API key")
    matrix_user_id: str = Field(..., max_length=255, description="Matrix user ID (e.g. @agent:matrix.example.com)")
    server_url: str = Field(..., max_length=512, description="NexusMatrix Server URL")
    sync_token: Optional[str] = Field(None, max_length=512, description="Incremental sync token (resume point)")
    next_poll_time: Optional[datetime] = Field(None, description="Next scheduled poll time (adaptive)")
    is_active: bool = Field(default=True, description="Whether Matrix is active for this Agent")
    created_at: Optional[datetime] = Field(None, description="Record creation time")
    updated_at: Optional[datetime] = Field(None, description="Last update time")


# ===== Table Manager =====

class MatrixCredentialsTableManager(BaseTableManager):
    """Table manager for matrix_credentials."""
    model = MatrixCredentialData
    table_name = "matrix_credentials"
    field_name_mapping = {"id": "id"}
    ignored_fields = {"created_at", "updated_at"}
    unique_id_field = "agent_id"


# ===== Table Creation SQL =====

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `matrix_credentials` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `agent_id` VARCHAR(64) NOT NULL COMMENT 'Agent ID (primary business key)',
    `nexus_agent_id` VARCHAR(64) NULL COMMENT 'NexusMatrix internal agent ID (agt_xxx)',
    `api_key` VARCHAR(255) NOT NULL COMMENT 'NexusMatrix API key',
    `matrix_user_id` VARCHAR(255) NOT NULL COMMENT 'Matrix user ID',
    `server_url` VARCHAR(512) NOT NULL COMMENT 'NexusMatrix Server URL',
    `sync_token` VARCHAR(512) NULL COMMENT 'Incremental sync token',
    `next_poll_time` DATETIME(6) NULL COMMENT 'Next scheduled poll time',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT 'Whether active',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_agent_id` (`agent_id`),
    UNIQUE KEY `uk_matrix_user_id` (`matrix_user_id`),
    INDEX `idx_is_active` (`is_active`),
    INDEX `idx_next_poll_time` (`next_poll_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Matrix credentials and polling state for Agents';
"""


# ===== Entry Point =====

async def create_matrix_credentials_table(force: bool = False) -> None:
    """Create the matrix_credentials table."""
    await create_table(
        table_name="matrix_credentials",
        create_sql=CREATE_TABLE_SQL,
        force=force,
    )


def main():
    parser = argparse.ArgumentParser(description="Create matrix_credentials table")
    parser.add_argument("--force", action="store_true", help="Drop and recreate if exists")
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(create_matrix_credentials_table(force=args.force))


if __name__ == "__main__":
    main()
