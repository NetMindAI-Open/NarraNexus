#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Base Table Manager - Abstract common table management logic

This base class provides common functionality for all create_*_table.py scripts:
1. Table structure sync (sync_table)
2. MySQL type mapping (get_mysql_type)
3. Pydantic field extraction (get_pydantic_fields)
4. Database column retrieval (get_existing_columns)

Subclasses only need to:
1. Define the Pydantic model
2. Configure table name, field mappings, etc.
3. Override get_mysql_type() to add custom type mappings (if needed)

Example:
    class AgentTableManager(BaseTableManager):
        model = Agent
        table_name = "agents"
        field_name_mapping = {"id": "id"}
        ignored_fields = {"agent_create_time", "agent_update_time"}
"""

from __future__ import annotations

import inspect
from abc import ABC
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Type, get_args, get_origin

from loguru import logger
from pydantic import BaseModel

# Utils - use global singleton AsyncDatabaseClient
from xyz_agent_context.utils.db_factory import get_db_client
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


class BaseTableManager(ABC):
    """
    Table Manager Base Class

    Provides common implementation for table structure definition and sync.
    For CRUD operations, use the repository layer.
    """

    # Subclasses must define these class attributes
    model: Type[BaseModel] = None  # Pydantic model class
    table_name: str = None  # Database table name
    field_name_mapping: Dict[str, str] = {}  # Mapping from Pydantic field names to database column names
    ignored_fields: set = set()  # Fields to ignore (e.g., timestamps)
    new_column_defaults: Dict[str, str] = {}  # Default values for new columns
    protected_columns: set = {"id", "create_time", "update_time", "created_at", "updated_at"}  # Protected columns

    # Unique identifier field (e.g., agent_id, user_id), auto-generates UNIQUE INDEX
    unique_id_field: str = None

    @classmethod
    def get_mysql_type(cls, field_name: str, field_type: type, field_info: Any) -> str:
        """
        Map Python types to MySQL types

        Subclasses can override this method to add specific field handling logic
        """
        origin = get_origin(field_type)
        args = get_args(field_type)

        # Handle Optional[T] type
        is_optional = False
        if origin is type(None) or (origin and args and type(None) in args):
            is_optional = True
            if args:
                actual_type = next((arg for arg in args if arg is not type(None)), None)
                if actual_type:
                    field_type = actual_type
                    origin = get_origin(field_type)
                    args = get_args(field_type)

        # ID field handling
        if field_name == "id":
            return "BIGINT UNSIGNED NOT NULL AUTO_INCREMENT"

        # Get default value from Field
        has_default = False
        default_value = None
        if hasattr(field_info, 'default') and field_info.default is not None:
            has_default = True
            default_value = field_info.default

        # Handle Enum type
        if inspect.isclass(field_type) and issubclass(field_type, Enum):
            enum_values = [f"'{member.value}'" for member in field_type]
            enum_str = ",".join(enum_values)
            null_str = 'NULL' if is_optional and not has_default else 'NOT NULL'

            if has_default and hasattr(default_value, 'value'):
                return f"ENUM({enum_str}) {null_str} DEFAULT '{default_value.value}'"
            return f"ENUM({enum_str}) {null_str}"

        # Common type mappings
        if field_type == str or field_type == "str":
            # Pydantic V2: max_length is stored in the MaxLen object within metadata
            max_length = None
            if hasattr(field_info, 'metadata'):
                for constraint in field_info.metadata:
                    if hasattr(constraint, 'max_length'):
                        max_length = constraint.max_length
                        break
            max_length = max_length or 255
            null_str = 'NULL' if is_optional else 'NOT NULL'
            default_str = f" DEFAULT '{default_value}'" if has_default and not is_optional else ""
            return f"VARCHAR({max_length}) {null_str}{default_str}"

        if field_type == int or field_type == "int":
            null_str = 'NULL' if is_optional else 'NOT NULL'
            default_str = f" DEFAULT {default_value}" if has_default and not is_optional else ""
            return f"BIGINT {null_str}{default_str}"

        if field_type == float or field_type == "float":
            null_str = 'NULL' if is_optional else 'NOT NULL'
            default_str = f" DEFAULT {default_value}" if has_default and not is_optional else ""
            return f"DOUBLE {null_str}{default_str}"

        if field_type == bool or field_type == "bool":
            null_str = 'NULL' if is_optional else 'NOT NULL'
            default_str = f" DEFAULT {1 if default_value else 0}" if has_default and not is_optional else ""
            return f"TINYINT(1) {null_str}{default_str}"

        if field_type == datetime or field_type == "datetime":
            null_str = 'NULL' if is_optional else 'NOT NULL'
            return f"DATETIME(6) {null_str}"

        if origin == dict or field_type == dict:
            return "JSON NULL"

        if origin == list or field_type == list:
            return "JSON NULL"

        if inspect.isclass(field_type) and issubclass(field_type, BaseModel):
            return "JSON NULL"

        # Default: TEXT type
        null_str = 'NULL' if is_optional else 'NOT NULL'
        return f"TEXT {null_str}"

    @classmethod
    def get_sqlite_type(cls, field_name: str, field_type: type, field_info: Any) -> str:
        """
        Map Python types to SQLite types.

        SQLite has a simplified type system compared to MySQL:
        - str -> TEXT
        - int -> INTEGER
        - float -> REAL
        - bool -> INTEGER (0/1)
        - datetime -> TEXT (ISO 8601)
        - dict, list -> TEXT (JSON stored as text)
        - bytes -> BLOB
        - Enum -> TEXT
        - default -> TEXT

        Subclasses can override this method to add custom field handling logic.
        """
        origin = get_origin(field_type)
        args = get_args(field_type)

        # Handle Optional[T] type
        is_optional = False
        if origin is type(None) or (origin and args and type(None) in args):
            is_optional = True
            if args:
                actual_type = next((arg for arg in args if arg is not type(None)), None)
                if actual_type:
                    field_type = actual_type
                    origin = get_origin(field_type)
                    args = get_args(field_type)

        # ID field handling (SQLite auto-increment primary key)
        if field_name == "id":
            return "INTEGER PRIMARY KEY AUTOINCREMENT"

        # Get default value from Field
        has_default = False
        default_value = None
        if hasattr(field_info, 'default') and field_info.default is not None:
            has_default = True
            default_value = field_info.default

        null_str = '' if is_optional else ' NOT NULL'

        # Handle Enum type -> TEXT
        if inspect.isclass(field_type) and issubclass(field_type, Enum):
            default_str = ""
            if has_default and hasattr(default_value, 'value'):
                default_str = f" DEFAULT '{default_value.value}'"
            return f"TEXT{null_str}{default_str}"

        # str -> TEXT
        if field_type == str or field_type == "str":
            default_str = f" DEFAULT '{default_value}'" if has_default and not is_optional else ""
            return f"TEXT{null_str}{default_str}"

        # int -> INTEGER
        if field_type == int or field_type == "int":
            default_str = f" DEFAULT {default_value}" if has_default and not is_optional else ""
            return f"INTEGER{null_str}{default_str}"

        # float -> REAL
        if field_type == float or field_type == "float":
            default_str = f" DEFAULT {default_value}" if has_default and not is_optional else ""
            return f"REAL{null_str}{default_str}"

        # bool -> INTEGER (0/1)
        if field_type == bool or field_type == "bool":
            default_str = f" DEFAULT {1 if default_value else 0}" if has_default and not is_optional else ""
            return f"INTEGER{null_str}{default_str}"

        # datetime -> TEXT (ISO 8601)
        if field_type == datetime or field_type == "datetime":
            return f"TEXT{null_str}"

        # bytes -> BLOB
        if field_type == bytes or field_type == "bytes":
            return f"BLOB{null_str}"

        # dict, list -> TEXT (JSON stored as text)
        if origin == dict or field_type == dict:
            return "TEXT"

        if origin == list or field_type == list:
            return "TEXT"

        # Pydantic BaseModel -> TEXT (JSON stored as text)
        if inspect.isclass(field_type) and issubclass(field_type, BaseModel):
            return "TEXT"

        # Default: TEXT
        return f"TEXT{null_str}"

    @classmethod
    def get_pydantic_fields(cls) -> Dict[str, tuple[type, Any]]:
        """Get Pydantic model field definitions (filtering out ignored fields)"""
        fields = {}
        for field_name, field in cls.model.model_fields.items():
            if field_name in cls.ignored_fields:
                continue
            fields[field_name] = (field.annotation, field)
        return fields

    @classmethod
    async def get_existing_columns(cls, db_client: AsyncDatabaseClient) -> Dict[str, str]:
        """Get existing columns of the database table"""
        query = f"SHOW COLUMNS FROM `{cls.table_name}`"
        results = await db_client.execute(query)

        columns = {}
        for row in results:
            if isinstance(row, dict):
                col_name = row["Field"]
                col_type = row["Type"]
            else:
                col_name = row[0]
                col_type = row[1]
            columns[col_name] = col_type

        return columns

    @classmethod
    async def sync_table(
        cls,
        dry_run: bool = False,
        db_client: Optional["AsyncDatabaseClient"] = None,
        auto_safe: bool = False,
    ) -> None:
        """
        Sync database table structure with Pydantic model

        Args:
            dry_run: Whether to only display changes without actually executing
            db_client: AsyncDatabaseClient instance (optional)
            auto_safe: If True, auto-apply safe changes (ENUM expansion, VARCHAR growth),
                       skip dangerous changes silently. No user prompts.
        """
        if db_client is None:
            db_client = await get_db_client()

        logger.info(f"\n{'='*60}")
        logger.info(f"Syncing table: {cls.table_name}")
        logger.info(f"Model: {cls.model.__name__}")
        logger.info(f"Dry run: {dry_run}")
        logger.info(f"{'='*60}\n")

        # Get Pydantic model fields
        pydantic_fields = cls.get_pydantic_fields()
        logger.info(f"Found {len(pydantic_fields)} fields in Pydantic model (excluding ignored fields)")

        # Get database table columns
        try:
            db_columns = await cls.get_existing_columns(db_client)
            logger.info(f"Found {len(db_columns)} columns in database table\n")
        except Exception as e:
            logger.error(f"Error reading table structure: {e}")
            logger.info(f"Make sure table '{cls.table_name}' exists in the database")
            return

        # Map Pydantic field names to database column names
        pydantic_to_db = {
            pydantic_name: cls.field_name_mapping.get(pydantic_name, pydantic_name)
            for pydantic_name in pydantic_fields.keys()
        }

        # Find columns that need to be added
        columns_to_add = []
        for pydantic_name, db_name in pydantic_to_db.items():
            if db_name not in db_columns:
                field_type, field_info = pydantic_fields[pydantic_name]
                mysql_type = cls.get_mysql_type(pydantic_name, field_type, field_info)
                default_value = cls.new_column_defaults.get(pydantic_name, "")
                columns_to_add.append((db_name, mysql_type, default_value))

        # Find columns whose type has changed (e.g., ENUM values added)
        # Each entry: (db_name, actual_type, expected_type, safety_level)
        # safety_level: "safe" = auto-apply, "dangerous" = warn + confirm per-column
        columns_to_modify = []
        for pydantic_name, db_name in pydantic_to_db.items():
            if db_name not in db_columns or db_name in cls.protected_columns:
                continue
            field_type, field_info = pydantic_fields[pydantic_name]
            expected_type = cls.get_mysql_type(pydantic_name, field_type, field_info)
            actual_type = db_columns[db_name]
            if cls._type_needs_modify(actual_type, expected_type):
                safety = cls._classify_modify_safety(actual_type, expected_type)
                columns_to_modify.append((db_name, actual_type, expected_type, safety))

        # Find columns that need to be dropped
        db_to_pydantic = {v: k for k, v in pydantic_to_db.items()}
        columns_to_drop = [
            col for col in db_columns.keys()
            if col not in db_to_pydantic and col not in cls.protected_columns
        ]

        # Display changes
        if columns_to_add:
            print("Columns to ADD:")
            for col_name, col_type, default in columns_to_add:
                default_str = f" (default: {default})" if default else ""
                print(f"   + {col_name}: {col_type}{default_str}")
        else:
            print("No columns to add")

        print()

        if columns_to_modify:
            print("Columns to MODIFY:")
            for col_name, old_type, new_type, safety in columns_to_modify:
                safety_tag = "⚠ DANGEROUS" if safety == "dangerous" else "✓ safe"
                print(f"   ~ {col_name}: {old_type} → {new_type}  [{safety_tag}]")
        else:
            print("No columns to modify")

        print()

        if columns_to_drop:
            print("Columns to DROP:")
            for col_name in columns_to_drop:
                print(f"   - {col_name}")
        else:
            print("No columns to drop")

        # In auto_safe mode, apply everything but collect dangerous changes for a summary notice
        dangerous_changes_applied = []
        if auto_safe:
            for col_name, old_type, new_type, safety in columns_to_modify:
                if safety == "dangerous":
                    dangerous_changes_applied.append((col_name, old_type, new_type))
            if columns_to_drop:
                for col_name in columns_to_drop:
                    dangerous_changes_applied.append((col_name, "DROP", "—"))

        # If no changes
        if not columns_to_add and not columns_to_modify and not columns_to_drop:
            print("\nTable structure is up-to-date!")
            return

        # Execute changes
        if dry_run:
            print("\nDRY RUN - No changes were made")
            return

        if not auto_safe:
            print("\n" + "="*60)
            proceed = input("Proceed with changes? (yes/no): ")
            if proceed.lower() != "yes":
                print("Aborted by user")
                return

        # Add new columns
        for col_name, col_type, default in columns_to_add:
            alter_sql = f"ALTER TABLE `{cls.table_name}` ADD COLUMN `{col_name}` {col_type}"
            if default != "":
                # JSON type default values need to be wrapped in single quotes (e.g., '[]', '{}')
                if col_type.startswith("JSON") or col_type in ("MEDIUMTEXT", "LONGTEXT", "TEXT"):
                    alter_sql += f" DEFAULT ('{default}')"
                elif isinstance(default, str) and not default.startswith("'"):
                    alter_sql += f" DEFAULT '{default}'"
                else:
                    alter_sql += f" DEFAULT {default}"

            print(f"   Executing: {alter_sql}")
            try:
                await db_client.execute(alter_sql, fetch=False)
                print(f"   Added column: {col_name}")
            except Exception as e:
                print(f"   Error adding column {col_name}: {e}")

        # Modify columns with type changes
        for col_name, old_type, new_type, safety in columns_to_modify:
            if safety == "dangerous" and not auto_safe:
                # Interactive mode: confirm per-column for dangerous changes
                count_sql = f"SELECT COUNT(*) as cnt FROM `{cls.table_name}` WHERE `{col_name}` IS NOT NULL"
                try:
                    result = await db_client.execute(count_sql)
                    row_count = result[0]["cnt"] if result else 0
                except Exception:
                    row_count = "unknown"

                print(f"\n   ⚠ WARNING: Column `{col_name}` has {row_count} rows with data.")
                print(f"     This change ({old_type} → {new_type}) may cause data loss!")
                confirm = input(f"     Apply this MODIFY to `{col_name}`? (yes/no): ")
                if confirm.lower() != "yes":
                    print(f"     Skipped column: {col_name}")
                    continue

            alter_sql = f"ALTER TABLE `{cls.table_name}` MODIFY COLUMN `{col_name}` {new_type}"
            print(f"   Executing: {alter_sql}")
            try:
                await db_client.execute(alter_sql, fetch=False)
                print(f"   Modified column: {col_name}")
            except Exception as e:
                print(f"   Error modifying column {col_name}: {e}")

        # Drop columns
        for col_name in columns_to_drop:
            alter_sql = f"ALTER TABLE `{cls.table_name}` DROP COLUMN `{col_name}`"
            print(f"   Executing: {alter_sql}")
            try:
                await db_client.execute(alter_sql, fetch=False)
                print(f"   Dropped column: {col_name}")
            except Exception as e:
                print(f"   Error dropping column {col_name}: {e}")

        # Show a friendly notice when dangerous changes were auto-applied
        if auto_safe and dangerous_changes_applied:
            print("\n" + "─"*60)
            print("  ⚠ Notice: the following schema changes were auto-applied:")
            for col_name, old_type, new_type in dangerous_changes_applied:
                if old_type == "DROP":
                    print(f"     DROP  {col_name}")
                else:
                    print(f"     MODIFY  {col_name}: {old_type} → {new_type}")
            print("  We're iterating fast — sorry for the inconvenience!")
            print("  Your data has been migrated automatically.")
            print("─"*60)

        print("\nTable sync completed!")

    @staticmethod
    def _classify_modify_safety(actual_mysql_type: str, expected_mysql_type: str) -> str:
        """
        Classify whether a MODIFY COLUMN operation is safe or dangerous.

        Returns:
            "safe"      — additive change, no data loss (e.g., ENUM expansion, VARCHAR growth)
            "dangerous" — may cause data loss (e.g., ENUM contraction, VARCHAR shrink, type change)
        """
        import re

        actual = actual_mysql_type.lower().strip()
        expected = expected_mysql_type.lower().strip()

        # --- ENUM: safe if only adding values, dangerous if removing ---
        actual_enum = re.match(r"enum\((.+?)\)", actual)
        expected_enum = re.match(r"enum\((.+?)\)", expected)
        if actual_enum and expected_enum:
            actual_vals = set(re.findall(r"'([^']*)'", actual_enum.group(1)))
            expected_vals = set(re.findall(r"'([^']*)'", expected_enum.group(1)))
            removed = actual_vals - expected_vals
            if not removed:
                return "safe"  # Only adding new values
            return "dangerous"  # Removing existing values

        # --- VARCHAR: safe if growing, dangerous if shrinking ---
        actual_vc = re.match(r"varchar\((\d+)\)", actual)
        expected_vc = re.match(r"varchar\((\d+)\)", expected)
        if actual_vc and expected_vc:
            if int(expected_vc.group(1)) >= int(actual_vc.group(1)):
                return "safe"
            return "dangerous"

        # --- Any other type change is dangerous by default ---
        return "dangerous"

    @staticmethod
    def _type_needs_modify(actual_mysql_type: str, expected_mysql_type: str) -> bool:
        """
        Compare MySQL column type from DB (e.g., "enum('pending','active','running')")
        with expected type from Pydantic model (e.g., "ENUM('pending','active','running','cancelled') NOT NULL DEFAULT 'pending'").

        Only returns True if the core type definition differs (ignoring NULL/DEFAULT clauses).
        """
        import re

        def extract_base_type(type_str: str) -> str:
            """Extract the base type, stripping NOT NULL / NULL / DEFAULT ... clauses."""
            s = type_str.lower().strip()
            # Remove DEFAULT clause (with its value) first, then NULL clauses
            # DEFAULT 'value', DEFAULT value, DEFAULT NULL
            s = re.sub(r"\s+default\s+\S+", "", s)
            s = re.sub(r"\s+not\s+null", "", s)
            s = re.sub(r"\s+null", "", s)
            # Remove all spaces for comparison
            return s.replace(" ", "").strip()

        return extract_base_type(actual_mysql_type) != extract_base_type(expected_mysql_type)
