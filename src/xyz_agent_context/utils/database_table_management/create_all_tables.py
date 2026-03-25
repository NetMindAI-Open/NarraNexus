#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Database Table Creation Tool

Creates all database tables at once.

Features:
1. Batch creation of all tables
2. Check if tables already exist
3. Support for selective creation of specified tables
4. Colored output and progress indicators

Usage:
    # Create all tables (if they don't exist)
    uv run python src/xyz_agent_context/utils/database_table_management/create_all_tables.py

    # Create only specified tables
    uv run python src/xyz_agent_context/utils/database_table_management/create_all_tables.py --tables agents users

    # Force rebuild all tables (dangerous! will delete data)
    uv run python src/xyz_agent_context/utils/database_table_management/create_all_tables.py --force

    # Test database connection
    uv run python src/xyz_agent_context/utils/database_table_management/create_all_tables.py --test-connection
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Type, Any

from loguru import logger

try:
    from xyz_agent_context.utils.database_table_management.create_agent_table import AgentTableManager
    from xyz_agent_context.utils.database_table_management.create_user_table import UserTableManager
    from xyz_agent_context.utils.database_table_management.create_event_table import EventTableManager
    from xyz_agent_context.utils.database_table_management.create_narrative_table import NarrativeTableManager
    from xyz_agent_context.utils.database_table_management.create_mcp_table import MCPTableManager
    from xyz_agent_context.utils.database_table_management.create_chat_table import InboxTableManager
    from xyz_agent_context.utils.database_table_management.create_agent_message_table import AgentMessageTableManager
    # Instance tables
    from xyz_agent_context.utils.database_table_management.create_module_instances_table import ModuleInstancesTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_social_entities_table import InstanceSocialEntitiesTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_jobs_table import InstanceJobsTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_rag_store_table import InstanceRAGStoreTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_narrative_links_table import InstanceNarrativeLinksTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_awareness_table import InstanceAwarenessTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_event_memory_table import InstanceModuleReportMemoryTableManager, InstanceJsonFormatMemoryTableManager
    from xyz_agent_context.utils.database_table_management.create_matrix_table import MatrixCredentialsTableManager
    from xyz_agent_context.utils.database_table_management.create_cost_records_table import create_cost_records_table
    from xyz_agent_context.utils.database_table_management.create_matrix_processed_events_table import create_matrix_processed_events_table
    from xyz_agent_context.utils.database_table_management.create_embeddings_store_table import create_embeddings_store_table
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        check_table_exists,
    )
    from xyz_agent_context.utils.db_factory import get_db_client
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.database_table_management.create_agent_table import AgentTableManager
    from xyz_agent_context.utils.database_table_management.create_user_table import UserTableManager
    from xyz_agent_context.utils.database_table_management.create_event_table import EventTableManager
    from xyz_agent_context.utils.database_table_management.create_narrative_table import NarrativeTableManager
    from xyz_agent_context.utils.database_table_management.create_mcp_table import MCPTableManager
    from xyz_agent_context.utils.database_table_management.create_chat_table import InboxTableManager
    from xyz_agent_context.utils.database_table_management.create_agent_message_table import AgentMessageTableManager
    # Instance tables
    from xyz_agent_context.utils.database_table_management.create_module_instances_table import ModuleInstancesTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_social_entities_table import InstanceSocialEntitiesTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_jobs_table import InstanceJobsTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_rag_store_table import InstanceRAGStoreTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_narrative_links_table import InstanceNarrativeLinksTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_awareness_table import InstanceAwarenessTableManager
    from xyz_agent_context.utils.database_table_management.create_instance_event_memory_table import InstanceModuleReportMemoryTableManager, InstanceJsonFormatMemoryTableManager
    from xyz_agent_context.utils.database_table_management.create_matrix_table import MatrixCredentialsTableManager
    from xyz_agent_context.utils.database_table_management.create_cost_records_table import create_cost_records_table
    from xyz_agent_context.utils.database_table_management.create_matrix_processed_events_table import create_matrix_processed_events_table
    from xyz_agent_context.utils.database_table_management.create_embeddings_store_table import create_embeddings_store_table
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        check_table_exists,
    )
    from xyz_agent_context.utils.db_factory import get_db_client


# ===== Table Configuration =====
# Format: (table_name, TableManager_class, index_list)

TABLE_CONFIGS: Dict[str, Tuple[Type, List[Tuple[str, List[str], bool]]]] = {
    # ===== Core Tables =====
    "agents": (
        AgentTableManager,
        [
            ("idx_created_by", ["created_by"], False),
            ("idx_agent_type", ["agent_type"], False),
            ("idx_create_time", ["agent_create_time"], False),
        ]
    ),
    "users": (
        UserTableManager,
        [
            ("idx_user_type", ["user_type"], False),
            ("idx_status", ["status"], False),
        ]
    ),
    "events": (
        EventTableManager,
        [
            ("idx_narrative_id", ["narrative_id"], False),
            ("idx_agent_id", ["agent_id"], False),
            ("idx_user_id", ["user_id"], False),
            ("idx_trigger", ["trigger"], False),
            ("idx_created_at", ["created_at"], False),
        ]
    ),
    "narratives": (
        NarrativeTableManager,
        [
            ("idx_agent_id", ["agent_id"], False),
            ("idx_type", ["type"], False),
            ("idx_created_at", ["created_at"], False),
        ]
    ),
    "mcp_urls": (
        MCPTableManager,
        [
            ("idx_agent_user", ["agent_id", "user_id"], False),
            ("idx_is_enabled", ["is_enabled"], False),
        ]
    ),
    "inbox_table": (
        InboxTableManager,
        [
            ("idx_user_id", ["user_id"], False),
            ("idx_is_read", ["is_read"], False),
        ]
    ),
    "agent_messages": (
        AgentMessageTableManager,
        [
            ("idx_agent_id", ["agent_id"], False),
            ("idx_agent_source", ["agent_id", "source_type"], False),
            ("idx_created_at", ["created_at"], False),
            ("idx_if_response", ["agent_id", "if_response"], False),
        ]
    ),
    # ===== Instance Tables =====
    "module_instances": (
        ModuleInstancesTableManager,
        [
            ("idx_agent_id", ["agent_id"], False),
            ("idx_agent_user", ["agent_id", "user_id"], False),
            ("idx_module_class", ["module_class"], False),
            ("idx_status", ["status"], False),
            ("idx_is_public", ["agent_id", "is_public"], False),
        ]
    ),
    "instance_social_entities": (
        InstanceSocialEntitiesTableManager,
        [
            ("uk_instance_entity", ["instance_id", "entity_id"], True),
            ("idx_instance_id", ["instance_id"], False),
            ("idx_entity_type", ["entity_type"], False),
        ]
    ),
    "instance_jobs": (
        InstanceJobsTableManager,
        [
            ("uk_instance_id", ["instance_id"], True),
            ("idx_agent_user", ["agent_id", "user_id"], False),
            ("idx_status", ["status"], False),
            ("idx_next_run_time", ["next_run_time"], False),
            ("idx_narrative_id", ["narrative_id"], False),
        ]
    ),
    "instance_rag_store": (
        InstanceRAGStoreTableManager,
        [
            ("uk_display_name", ["display_name"], True),
        ]
    ),
    "instance_narrative_links": (
        InstanceNarrativeLinksTableManager,
        [
            ("uk_instance_narrative", ["instance_id", "narrative_id"], True),
            ("idx_narrative_id", ["narrative_id"], False),
            ("idx_instance_id", ["instance_id"], False),
            ("idx_link_type", ["link_type"], False),
        ]
    ),
    "instance_awareness": (
        InstanceAwarenessTableManager,
        []
    ),
    "instance_module_report_memory": (
        InstanceModuleReportMemoryTableManager,
        []
    ),
    "instance_json_format_memory": (
        InstanceJsonFormatMemoryTableManager,
        []
    ),
    # ===== Matrix Tables =====
    "matrix_credentials": (
        MatrixCredentialsTableManager,
        [
            ("idx_is_active", ["is_active"], False),
            ("idx_next_poll_time", ["next_poll_time"], False),
        ]
    ),
}


# ===== Create Functions =====

async def create_single_table(
    table_name: str,
    manager_class: Type,
    indexes: List[Tuple[str, List[str], bool]],
    force: bool = False
) -> Tuple[str, bool, str]:
    """
    Create a single table

    Returns:
        (table_name, success, status_message)
    """
    try:
        exists = await check_table_exists(table_name)

        if exists and not force:
            return (table_name, True, "already exists (skipped)")

        success = await create_table(manager_class, indexes, force=force)

        if success:
            return (table_name, True, "created successfully" if not exists else "rebuilt successfully")
        else:
            return (table_name, True, "already exists (skipped)")

    except Exception as e:
        logger.exception(f"Error creating table {table_name}")
        return (table_name, False, f"failed: {str(e)[:50]}")


async def create_all_tables(
    tables: List[str] = None,
    force: bool = False
) -> None:
    """
    Create all tables or specified tables

    Args:
        tables: List of tables to create (None means create all tables)
        force: Whether to force rebuild
    """
    # Determine which tables to create
    if tables is None:
        tables_to_create = list(TABLE_CONFIGS.keys())
    else:
        tables_to_create = [t for t in tables if t in TABLE_CONFIGS]
        invalid_tables = [t for t in tables if t not in TABLE_CONFIGS]
        if invalid_tables:
            print(f"Warning: unknown tables: {', '.join(invalid_tables)}")

    if not tables_to_create:
        print("No tables to create")
        return

    # Display overview
    print("\n" + "="*80)
    print("Database Table Creation Tool")
    print("="*80)
    print(f"\nMode: {'Force rebuild (will delete existing data!)' if force else 'Only create non-existing tables'}")
    print(f"Tables to process: {', '.join(tables_to_create)}")
    print(f"Total: {len(tables_to_create)} tables")

    if force:
        print("\n" + "!"*40)
        print("WARNING: Force rebuild will delete all existing data!")
        print("!"*40)
        confirm = input("\nConfirm? (type 'DELETE ALL DATA' to confirm): ")
        if confirm != "DELETE ALL DATA":
            print("Operation cancelled")
            return

    # Create each table
    results = []
    for i, table_name in enumerate(tables_to_create, 1):
        print(f"\n[{i}/{len(tables_to_create)}] Processing table: {table_name}")
        manager_class, indexes = TABLE_CONFIGS[table_name]
        result = await create_single_table(table_name, manager_class, indexes, force)
        results.append(result)

    # Raw-SQL tables (not managed by BaseTableManager)
    raw_sql_tables = {
        "cost_records": create_cost_records_table,
        "matrix_processed_events": create_matrix_processed_events_table,
        "embeddings_store": create_embeddings_store_table,
    }
    for raw_table_name, create_fn in raw_sql_tables.items():
        if tables is not None and raw_table_name not in tables:
            continue
        idx = len(results) + 1
        print(f"\n[{idx}/{len(tables_to_create) + len(raw_sql_tables)}] Processing table: {raw_table_name} (raw SQL)")
        try:
            exists = await check_table_exists(raw_table_name)
            if exists and not force:
                results.append((raw_table_name, True, "already exists (skipped)"))
            else:
                await create_fn(force=force)
                results.append((raw_table_name, True, "created successfully"))
        except Exception as e:
            results.append((raw_table_name, False, f"failed: {str(e)[:50]}"))

    # Display summary
    print("\n\n" + "="*80)
    print("Creation Results Summary")
    print("="*80)

    success_count = sum(1 for _, success, _ in results if success)
    failed_count = len(results) - success_count

    for table_name, success, message in results:
        status = "OK" if success else "FAIL"
        print(f"  [{status:4}] {table_name:25} - {message}")

    print(f"\nTotal: {len(results)} tables")
    print(f"Succeeded: {success_count}")
    if failed_count > 0:
        print(f"Failed: {failed_count}")

    print("\n" + "="*80)
    print("Done!")
    print("="*80 + "\n")


# ===== Database Connection Test =====

async def test_database_connection() -> bool:
    """Test database connection"""
    try:
        print("Testing database connection...")
        db = await get_db_client()
        is_connected = await db.ping()

        if is_connected:
            print("Database connection successful")
            return True
        else:
            print("Database connection failed")
            return False

    except Exception as e:
        print(f"Database connection error: {e}")
        logger.exception("Database connection error")
        return False


# ===== Command Line Interface =====

async def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Unified database table creation tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create all non-existing tables
  %(prog)s

  # Create only specified tables
  %(prog)s --tables agents users events

  # Force rebuild all tables (dangerous!)
  %(prog)s --force

  # Test database connection
  %(prog)s --test-connection

Supported tables:
  agents                        - Agent table
  users                         - User table
  events                        - Event table
  narratives                    - Narrative table
  mcp_urls                      - MCP URLs table
  inbox_table                   - Inbox table
  agent_messages                - Agent Messages table
  module_instances              - Module Instances table
  instance_social_entities      - Social Network entities table
  instance_jobs                 - Job table
  instance_rag_store            - RAG Store table
  instance_narrative_links      - Narrative Links table
  instance_awareness            - Awareness table
  instance_module_report_memory - Module Report Memory table
  instance_json_format_memory   - JSON Format Memory table
        """
    )

    parser.add_argument(
        "--tables", "-t",
        nargs="+",
        choices=list(TABLE_CONFIGS.keys()),
        help="Specify tables to create (default: create all tables)"
    )

    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force drop existing tables and recreate (dangerous!)"
    )

    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Test database connection only"
    )

    args = parser.parse_args()

    # Test database connection
    if args.test_connection:
        is_connected = await test_database_connection()
        sys.exit(0 if is_connected else 1)

    # Test connection
    is_connected = await test_database_connection()
    if not is_connected:
        print("\nDatabase connection failed, cannot continue")
        sys.exit(1)

    print()  # Empty line

    # Create tables
    await create_all_tables(
        tables=args.tables,
        force=args.force
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nOperation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError occurred: {e}")
        logger.exception("Unexpected error")
        sys.exit(1)
