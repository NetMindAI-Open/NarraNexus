#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create instance_jobs table

Contains JobData data model and InstanceJobsTableManager definition.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_instance_jobs_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_instance_jobs_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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


# ===== JobData Pydantic Model =====

class JobData(BaseModel):
    """
    Job Data Model

    Stores detailed information of a Job task.
    Each record is associated with a ModuleInstance (JobModule Instance).
    Instance and Job have a 1:1 relationship.
    """
    id: Optional[int] = None
    instance_id: str = Field(..., max_length=64, description="Associated ModuleInstance ID (JobModule)")
    job_id: str = Field(..., max_length=64, description="Job unique identifier")
    agent_id: str = Field(..., max_length=128, description="Owning Agent ID")
    user_id: str = Field(..., max_length=64, description="Owning User ID")
    title: str = Field(..., max_length=255, description="Job title")
    description: str = Field(default="", description="Job detailed description")
    payload: str = Field(default="", description="Execution instruction (natural language)")
    job_type: str = Field(..., max_length=32, description="Job type: one_off | scheduled | ongoing")
    trigger_config: Dict[str, Any] = Field(default_factory=dict, description="Trigger configuration JSON")
    status: str = Field(default="pending", description="Status: pending, active, running, completed, failed")
    process: List[str] = Field(default_factory=list, description="Execution process records (event_id list)")
    last_error: Optional[str] = Field(default=None, description="Last error message")
    notification_method: str = Field(default="inbox", description="Notification method")
    next_run_time: Optional[datetime] = Field(default=None, description="Next execution time")
    last_run_time: Optional[datetime] = Field(default=None, description="Last execution time")
    started_at: Optional[datetime] = Field(default=None, description="Execution start time")
    embedding: Optional[List[float]] = Field(default=None, description="Semantic embedding vector")
    related_entity_id: Optional[str] = Field(default=None, max_length=64, description="Target user ID")
    narrative_id: Optional[str] = Field(default=None, max_length=64, description="Associated Narrative ID")
    monitored_job_ids: Optional[List[str]] = Field(default=None, description="Monitored other Job IDs")
    iteration_count: int = Field(default=0, description="ONGOING type: current execution count")
    created_at: Optional[datetime] = Field(default=None, description="Creation time")
    updated_at: Optional[datetime] = Field(default=None, description="Update time")


# ===== Instance Jobs Table Manager =====

class InstanceJobsTableManager(BaseTableManager):
    """instance_jobs table manager"""
    model = JobData
    table_name = "instance_jobs"
    field_name_mapping = {}
    ignored_fields = set()
    protected_columns = {"id"}
    new_column_defaults = {}

    unique_id_field = "job_id"
    json_fields = {"trigger_config", "process", "embedding", "monitored_job_ids"}

    @classmethod
    def get_mysql_type(cls, field_name: str, field_type: type, field_info: Any) -> str:
        """Custom type mapping"""
        if field_name == "instance_id":
            return "VARCHAR(64) NOT NULL"
        if field_name == "job_id":
            return "VARCHAR(64) NOT NULL"
        if field_name == "agent_id":
            return "VARCHAR(128) NOT NULL"
        if field_name == "user_id":
            return "VARCHAR(64) NOT NULL"
        if field_name == "title":
            return "VARCHAR(255) NOT NULL"
        if field_name in ("description", "payload", "last_error"):
            return "TEXT"
        if field_name == "job_type":
            return "ENUM('one_off', 'scheduled', 'ongoing') NOT NULL"
        if field_name == "status":
            return "ENUM('pending', 'active', 'running', 'paused', 'completed', 'failed', 'cancelled') NOT NULL DEFAULT 'pending'"
        if field_name == "notification_method":
            return "VARCHAR(32) DEFAULT 'inbox'"
        if field_name == "embedding":
            return "MEDIUMTEXT"
        if field_name in ("trigger_config", "process"):
            return "JSON"
        if field_name in ("next_run_time", "last_run_time", "started_at", "created_at", "updated_at"):
            return "DATETIME(6)"
        if field_name in ("related_entity_id", "narrative_id"):
            return "VARCHAR(64) DEFAULT NULL"
        if field_name == "monitored_job_ids":
            return "JSON"
        if field_name == "iteration_count":
            return "INT DEFAULT 0"
        return super().get_mysql_type(field_name, field_type, field_info)


# ===== Index Definitions =====

INDEXES = [
    ("uk_instance_id", ["instance_id"], True),
    ("idx_agent_user", ["agent_id", "user_id"], False),
    ("idx_status", ["status"], False),
    ("idx_next_run_time", ["next_run_time"], False),
    ("idx_narrative_id", ["narrative_id"], False),
]


# ===== CLI =====

async def main():
    parser = argparse.ArgumentParser(description="Create instance_jobs table")
    parser.add_argument("--force", "-f", action="store_true", help="Force drop existing table and recreate")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("instance_jobs Table Creation Tool")
    print("=" * 60)

    if args.interactive:
        await create_table_interactive(InstanceJobsTableManager, INDEXES)
    else:
        await create_table(InstanceJobsTableManager, INDEXES, force=args.force)


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
