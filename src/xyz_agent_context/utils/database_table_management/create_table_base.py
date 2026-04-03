#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create Table Base - Base utilities for table creation

Provides common logic for creating database tables:
1. Check if table exists
2. Generate CREATE TABLE SQL from Pydantic model
3. Create table and set up indexes

Usage:
    Subclasses only need to define a TableManager class, then call the create_table() method
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Type, Optional, List, Tuple

from loguru import logger

try:
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager


async def check_table_exists(table_name: str) -> bool:
    """
    Check if a table exists

    Args:
        table_name: Table name

    Returns:
        True if the table exists, otherwise False
    """
    db_client = await get_db_client()

    query = """
        SELECT COUNT(*) as cnt
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
        AND table_name = %s
    """

    result = await db_client.execute(query, params=(table_name,), fetch=True)

    if result and len(result) > 0:
        return result[0].get("cnt", 0) > 0

    return False


async def check_table_exists_sqlite(table_name: str, db_client) -> bool:
    """
    Check if a table exists in a SQLite database.

    Uses sqlite_master to check for table existence without requiring
    information_schema (which is MySQL-specific).

    Args:
        table_name: Table name to check.
        db_client: A DatabaseBackend instance (SQLiteBackend).

    Returns:
        True if the table exists, otherwise False.
    """
    rows = await db_client.execute(
        "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return rows[0]["cnt"] > 0 if rows else False


def generate_create_table_sql(
    manager_class: Type[BaseTableManager],
    indexes: Optional[List[Tuple[str, List[str], bool]]] = None,
    engine: str = "InnoDB",
    charset: str = "utf8mb4",
    collate: str = "utf8mb4_unicode_ci"
) -> str:
    """
    Generate CREATE TABLE SQL from a TableManager

    Args:
        manager_class: TableManager class
        indexes: Index list, each item is (index_name, column_list, is_unique)
        engine: Storage engine
        charset: Character set
        collate: Collation

    Returns:
        CREATE TABLE SQL statement
    """
    table_name = manager_class.table_name
    pydantic_fields = manager_class.get_pydantic_fields()

    # Generate column definitions
    columns = []

    # First add auto-increment id primary key column
    columns.append("`id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT")

    # Add other columns
    for field_name, (field_type, field_info) in pydantic_fields.items():
        # If id maps to itself (e.g., Agent's id: Optional[int]), skip (auto-increment column already added)
        # If id maps to another column (e.g., Event's id -> event_id), need to generate that business ID column
        if field_name == 'id':
            mapped_name = manager_class.field_name_mapping.get('id', 'id')
            if mapped_name == 'id':
                continue
            # Maps to another column name, continue processing to generate business ID column

        db_column_name = manager_class.field_name_mapping.get(field_name, field_name)
        # Call get_mysql_type with db_column_name to ensure subclass overrides (e.g., checking "event_id") match correctly
        mysql_type = manager_class.get_mysql_type(db_column_name, field_type, field_info)
        columns.append(f"`{db_column_name}` {mysql_type}")

    # Add timestamp columns (timestamps in ignored_fields are auto-managed by the database)
    if 'agent_create_time' in manager_class.ignored_fields:
        columns.append("`agent_create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)")
        columns.append("`agent_update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")
    elif 'create_time' in manager_class.ignored_fields:
        columns.append("`create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)")
        columns.append("`update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")
    elif 'created_at' in manager_class.ignored_fields:
        columns.append("`created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)")
        columns.append("`updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")

    # Add primary key
    columns.append("PRIMARY KEY (`id`)")

    # Add indexes
    if indexes:
        for idx_name, idx_columns, is_unique in indexes:
            unique_str = "UNIQUE " if is_unique else ""
            cols_str = ", ".join(f"`{c}`" for c in idx_columns)
            columns.append(f"{unique_str}INDEX `{idx_name}` ({cols_str})")

    # If there's a unique identifier field, automatically add a unique index
    if manager_class.unique_id_field:
        idx_name = f"idx_{manager_class.unique_id_field}"
        columns.append(f"UNIQUE INDEX `{idx_name}` (`{manager_class.unique_id_field}`)")

    # Assemble SQL
    columns_sql = ",\n    ".join(columns)
    sql = f"""CREATE TABLE `{table_name}` (
    {columns_sql}
) ENGINE={engine} DEFAULT CHARSET={charset} COLLATE={collate};"""

    return sql


async def create_table(
    manager_class: Type[BaseTableManager],
    indexes: Optional[List[Tuple[str, List[str], bool]]] = None,
    force: bool = False
) -> bool:
    """
    Create a database table

    Args:
        manager_class: TableManager class
        indexes: Additional index list
        force: If True, drop existing table and recreate

    Returns:
        True if table was created successfully, False if table already exists
    """
    table_name = manager_class.table_name

    print(f"\n{'='*60}")
    print(f"Creating table: {table_name}")
    print(f"{'='*60}")

    # Check if table exists
    exists = await check_table_exists(table_name)

    if exists and not force:
        print(f"\nTable `{table_name}` already exists, no need to create.")
        print(f"To modify table structure, use sync_all_tables.py --tables {table_name}")
        return False

    db_client = await get_db_client()

    if exists and force:
        print(f"\nWARNING: About to drop existing table `{table_name}`!")
        confirm = input("Confirm deletion? (yes/no): ")
        if confirm.lower() != "yes":
            print("Operation cancelled")
            return False

        await db_client.execute(f"DROP TABLE IF EXISTS `{table_name}`", fetch=False)
        print(f"Dropped table `{table_name}`")

    # Generate and execute CREATE TABLE SQL
    create_sql = generate_create_table_sql(manager_class, indexes)

    print("\nExecuting SQL:")
    print("-" * 60)
    print(create_sql)
    print("-" * 60)

    try:
        await db_client.execute(create_sql, fetch=False)
        print(f"\nTable `{table_name}` created successfully!")
        return True
    except Exception as e:
        print(f"\nFailed to create table: {e}")
        logger.exception(f"Failed to create table {table_name}")
        return False


async def create_table_interactive(
    manager_class: Type[BaseTableManager],
    indexes: Optional[List[Tuple[str, List[str], bool]]] = None
) -> bool:
    """
    Interactive table creation

    Args:
        manager_class: TableManager class
        indexes: Additional index list

    Returns:
        Operation result
    """
    table_name = manager_class.table_name

    # Check if table exists
    exists = await check_table_exists(table_name)

    if exists:
        print(f"\nTable `{table_name}` already exists.")
        print("\nAvailable operations:")
        print("  1. Sync table structure (sync)")
        print("  2. Drop and rebuild table (force create)")
        print("  3. Exit")

        choice = input("\nPlease choose (1/2/3): ")

        if choice == "1":
            print(f"\nPlease run: uv run python -m xyz_agent_context.utils.database_table_management.sync_all_tables --tables {table_name}")
            return False
        elif choice == "2":
            return await create_table(manager_class, indexes, force=True)
        else:
            print("Operation cancelled")
            return False

    # Table does not exist, create it
    return await create_table(manager_class, indexes)
