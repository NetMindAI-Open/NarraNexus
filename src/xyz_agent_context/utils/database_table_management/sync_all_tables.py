#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Table Structure Sync Tool

Sync all database table structures at once (Agent, User, Event, Narrative)

Features:
1. Batch sync all table structures
2. Support dry-run mode (view changes without executing)
3. Support selective sync of specified tables
4. Colored output and progress indicators

Usage:
    # Sync all tables (dry-run mode)
    uv run python src/xyz_agent_context/utils/sync_all_tables.py --dry-run

    # Sync all tables (actual execution)
    uv run python src/xyz_agent_context/utils/sync_all_tables.py

    # Sync only specified tables
    uv run python src/xyz_agent_context/utils/sync_all_tables.py --tables agents users

    # Interactive sync (confirm each table individually)
    uv run python src/xyz_agent_context/utils/sync_all_tables.py --interactive
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List

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
    from xyz_agent_context.utils.db_factory import get_db_client


# ===== Table Managers Configuration =====

TABLE_MANAGERS = {
    # Core tables
    "agents": AgentTableManager,
    "users": UserTableManager,
    "events": EventTableManager,
    "narratives": NarrativeTableManager,
    "mcp_urls": MCPTableManager,
    "inbox_table": InboxTableManager,
    "agent_messages": AgentMessageTableManager,
    # Instance tables
    "module_instances": ModuleInstancesTableManager,
    "instance_social_entities": InstanceSocialEntitiesTableManager,
    "instance_jobs": InstanceJobsTableManager,
    "instance_rag_store": InstanceRAGStoreTableManager,
    "instance_narrative_links": InstanceNarrativeLinksTableManager,
    "instance_awareness": InstanceAwarenessTableManager,
    "instance_module_report_memory": InstanceModuleReportMemoryTableManager,
    "instance_json_format_memory": InstanceJsonFormatMemoryTableManager,
    # Matrix tables
    "matrix_credentials": MatrixCredentialsTableManager,
}


# ===== Sync Functions =====

async def sync_single_table(
    table_name: str,
    manager_class,
    dry_run: bool = False,
    interactive: bool = False,
    auto_safe: bool = False,
) -> bool:
    """
    Sync a single table

    Args:
        table_name: Table name
        manager_class: Table manager class
        dry_run: Whether in dry-run mode
        interactive: Whether to interactively confirm
        auto_safe: Auto-apply safe changes, skip dangerous ones

    Returns:
        Whether successful
    """
    print("\n" + "="*80)
    print(f"📊 Syncing table: {table_name}")
    print("="*80)

    try:
        if interactive and not dry_run and not auto_safe:
            print(f"\nChecking changes for {table_name} table...")
            await manager_class.sync_table(dry_run=True)

            proceed = input(f"\nSync {table_name} table? (yes/no): ")
            if proceed.lower() != "yes":
                print(f"Skipped {table_name} table")
                return True

        await manager_class.sync_table(dry_run=dry_run, auto_safe=auto_safe)
        print(f"\n{table_name} table sync {'check ' if dry_run else ''}completed")
        return True

    except Exception as e:
        print(f"\n{table_name} table sync failed: {e}")
        logger.exception(f"Error syncing table {table_name}")
        return False


async def sync_all_tables(
    tables: List[str] = None,
    dry_run: bool = False,
    interactive: bool = False,
    auto_safe: bool = False,
) -> None:
    """
    Sync all tables or specified tables

    Args:
        tables: List of tables to sync (None means sync all tables)
        dry_run: Whether in dry-run mode
        interactive: Whether to interactively confirm each table
        auto_safe: Auto-apply safe changes (ENUM expansion, VARCHAR growth),
                   skip dangerous changes silently. No user prompts.
    """
    # Determine which tables to sync
    if tables is None:
        tables_to_sync = list(TABLE_MANAGERS.keys())
    else:
        tables_to_sync = [t for t in tables if t in TABLE_MANAGERS]
        invalid_tables = [t for t in tables if t not in TABLE_MANAGERS]
        if invalid_tables:
            print(f"Warning: unknown tables: {', '.join(invalid_tables)}")

    if not tables_to_sync:
        print("No tables to sync")
        return

    # Display overview
    print("\n" + "="*80)
    print("Batch Table Structure Sync Tool")
    print("="*80)
    print(f"\nMode: {'DRY RUN (view changes only)' if dry_run else 'Actual execution'}")
    print(f"Tables to sync: {', '.join(tables_to_sync)}")
    print(f"Total: {len(tables_to_sync)} tables")

    if not dry_run and not interactive and not auto_safe:
        print("\n" + "WARNING "*5)
        print("WARNING: About to actually modify database table structure!")
        print("WARNING "*5)
        proceed = input("\nConfirm to proceed? (yes/no): ")
        if proceed.lower() != "yes":
            print("Operation cancelled")
            return

    # Sync each table
    results = {}
    for i, table_name in enumerate(tables_to_sync, 1):
        print(f"\n\n[{i}/{len(tables_to_sync)}] Processing table: {table_name}")
        manager_class = TABLE_MANAGERS[table_name]
        success = await sync_single_table(table_name, manager_class, dry_run, interactive, auto_safe)
        results[table_name] = success

    # Display summary
    print("\n\n" + "="*80)
    print("Sync Results Summary")
    print("="*80)

    success_count = sum(1 for v in results.values() if v)
    failed_count = len(results) - success_count

    for table_name, success in results.items():
        status = "OK" if success else "FAIL"
        print(f"  {table_name:15} {status}")

    print(f"\nTotal: {len(results)} tables")
    print(f"Succeeded: {success_count}")
    if failed_count > 0:
        print(f"Failed: {failed_count}")

    print("\n" + "="*80)
    if dry_run:
        print("Hint: This is dry-run mode, no actual database modifications were made")
        print("   Remove the --dry-run flag to actually execute the sync")
    else:
        print("All tables synced successfully!")
    print("="*80 + "\n")


# ===== Schema Change Detection (for automated checks) =====

async def check_schema_changes() -> bool:
    """
    Detect schema differences between Pydantic models and database tables.

    Checks for: new columns, dropped columns, and type changes (e.g., ENUM expansion).

    Returns:
        True = changes detected, False = no changes
    """
    has_changes = False

    for table_name, manager_class in TABLE_MANAGERS.items():
        try:
            db_client = await get_db_client()
            pydantic_fields = manager_class.get_pydantic_fields()
            try:
                db_columns = await manager_class.get_existing_columns(db_client)
            except Exception:
                # Table does not exist yet — handled by create_all_tables
                continue

            pydantic_to_db = {
                name: manager_class.field_name_mapping.get(name, name)
                for name in pydantic_fields.keys()
            }

            # Detect new columns
            columns_to_add = [
                db_name for _, db_name in pydantic_to_db.items()
                if db_name not in db_columns
            ]

            # Detect dropped columns
            db_to_pydantic = {v: k for k, v in pydantic_to_db.items()}
            columns_to_drop = [
                col for col in db_columns.keys()
                if col not in db_to_pydantic and col not in manager_class.protected_columns
            ]

            # Detect type changes (e.g., ENUM values added/removed, VARCHAR resized)
            columns_to_modify = []
            for pydantic_name, db_name in pydantic_to_db.items():
                if db_name not in db_columns or db_name in manager_class.protected_columns:
                    continue
                field_type, field_info = pydantic_fields[pydantic_name]
                expected_type = manager_class.get_mysql_type(pydantic_name, field_type, field_info)
                actual_type = db_columns[db_name]
                if manager_class._type_needs_modify(actual_type, expected_type):
                    columns_to_modify.append((db_name, actual_type, expected_type))

            if columns_to_add or columns_to_drop or columns_to_modify:
                has_changes = True
                print(f"  {table_name}:")
                for col in columns_to_add:
                    print(f"    + {col}")
                for col in columns_to_drop:
                    print(f"    - {col}")
                for col_name, old_type, new_type in columns_to_modify:
                    print(f"    ~ {col_name}: {old_type} → {new_type}")

        except Exception:
            continue

    return has_changes


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
        description="Unified database table structure sync tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run mode (view changes only, no execution)
  %(prog)s --dry-run

  # Sync all tables
  %(prog)s

  # Sync only specified tables
  %(prog)s --tables agents users

  # Interactive sync (confirm each table individually)
  %(prog)s --interactive

  # Test database connection
  %(prog)s --test-connection

Supported tables:
  agents                  - Agent table
  users                   - User table
  events                  - Event table
  narratives              - Narrative table
  social_network_entities - Social Network entities table
  mcp_urls                - MCP URLs table
        """
    )

    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Dry-run mode: only show changes, do not actually execute"
    )

    parser.add_argument(
        "--tables", "-t",
        nargs="+",
        choices=list(TABLE_MANAGERS.keys()),
        help="Specify tables to sync (default: sync all tables)"
    )

    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Interactive mode: confirm each table individually"
    )

    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Only test database connection"
    )

    parser.add_argument(
        "--auto-safe",
        action="store_true",
        help="Auto-apply safe changes (ENUM expansion, VARCHAR growth), skip dangerous ones. No prompts."
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if schema changes exist (exit code 0=has changes, 1=no changes)"
    )

    args = parser.parse_args()

    # Check mode: detect changes and exit with code
    if args.check:
        is_connected = await test_database_connection()
        if not is_connected:
            sys.exit(1)
        has_changes = await check_schema_changes()
        sys.exit(0 if has_changes else 1)

    # Test database connection
    if args.test_connection:
        is_connected = await test_database_connection()
        sys.exit(0 if is_connected else 1)

    # Test connection
    is_connected = await test_database_connection()
    if not is_connected:
        print("\nDatabase connection failed, cannot continue")
        sys.exit(1)

    print()  # blank line

    # Sync tables
    await sync_all_tables(
        tables=args.tables,
        dry_run=args.dry_run,
        interactive=args.interactive,
        auto_safe=args.auto_safe,
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
