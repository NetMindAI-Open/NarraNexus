"""
@file_name: test_table_creation_sqlite.py
@author: NexusAgent
@date: 2026-04-02
@description: Tests for SQLite type mapping in BaseTableManager.get_sqlite_type()

Verifies that each Python type maps to the correct SQLite column type,
and that check_table_exists_sqlite works correctly.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from xyz_agent_context.utils.database_table_management.table_manager_base import (
    BaseTableManager,
)
from xyz_agent_context.utils.database_table_management.create_table_base import (
    check_table_exists_sqlite,
)
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend


# --- Helpers ---

def _make_field_info(default=None, metadata=None):
    """Create a mock field_info with optional default and metadata."""
    fi = MagicMock()
    fi.default = default
    fi.metadata = metadata or []
    return fi


class SampleEnum(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class NestedModel(BaseModel):
    foo: str = "bar"


# --- Type mapping tests ---

class TestGetSqliteType:
    """Tests for BaseTableManager.get_sqlite_type()."""

    def test_id_field(self):
        """id field maps to INTEGER PRIMARY KEY AUTOINCREMENT."""
        result = BaseTableManager.get_sqlite_type("id", int, _make_field_info())
        assert result == "INTEGER PRIMARY KEY AUTOINCREMENT"

    def test_str_type(self):
        """str maps to TEXT NOT NULL."""
        result = BaseTableManager.get_sqlite_type("name", str, _make_field_info())
        assert result == "TEXT NOT NULL"

    def test_str_type_with_default(self):
        """str with default includes DEFAULT clause."""
        result = BaseTableManager.get_sqlite_type("name", str, _make_field_info(default="hello"))
        assert result == "TEXT NOT NULL DEFAULT 'hello'"

    def test_optional_str_type(self):
        """Optional[str] maps to TEXT (nullable)."""
        result = BaseTableManager.get_sqlite_type("name", Optional[str], _make_field_info())
        assert result == "TEXT"

    def test_int_type(self):
        """int maps to INTEGER NOT NULL."""
        result = BaseTableManager.get_sqlite_type("count", int, _make_field_info())
        assert result == "INTEGER NOT NULL"

    def test_int_type_with_default(self):
        """int with default includes DEFAULT clause."""
        result = BaseTableManager.get_sqlite_type("count", int, _make_field_info(default=0))
        assert result == "INTEGER NOT NULL DEFAULT 0"

    def test_float_type(self):
        """float maps to REAL NOT NULL."""
        result = BaseTableManager.get_sqlite_type("score", float, _make_field_info())
        assert result == "REAL NOT NULL"

    def test_float_type_with_default(self):
        """float with default includes DEFAULT clause."""
        result = BaseTableManager.get_sqlite_type("score", float, _make_field_info(default=0.5))
        assert result == "REAL NOT NULL DEFAULT 0.5"

    def test_bool_type(self):
        """bool maps to INTEGER NOT NULL."""
        result = BaseTableManager.get_sqlite_type("active", bool, _make_field_info())
        assert result == "INTEGER NOT NULL"

    def test_bool_type_with_default_true(self):
        """bool with default True maps to INTEGER NOT NULL DEFAULT 1."""
        result = BaseTableManager.get_sqlite_type("active", bool, _make_field_info(default=True))
        assert result == "INTEGER NOT NULL DEFAULT 1"

    def test_bool_type_with_default_false(self):
        """bool with default False maps to INTEGER NOT NULL DEFAULT 0."""
        result = BaseTableManager.get_sqlite_type("active", bool, _make_field_info(default=False))
        assert result == "INTEGER NOT NULL DEFAULT 0"

    def test_datetime_type(self):
        """datetime maps to TEXT NOT NULL (ISO 8601)."""
        result = BaseTableManager.get_sqlite_type("created_at", datetime, _make_field_info())
        assert result == "TEXT NOT NULL"

    def test_optional_datetime_type(self):
        """Optional[datetime] maps to TEXT (nullable)."""
        result = BaseTableManager.get_sqlite_type("updated_at", Optional[datetime], _make_field_info())
        assert result == "TEXT"

    def test_dict_type(self):
        """dict maps to TEXT (JSON)."""
        result = BaseTableManager.get_sqlite_type("metadata", dict, _make_field_info())
        assert result == "TEXT"

    def test_typed_dict_type(self):
        """Dict[str, Any] maps to TEXT (JSON)."""
        result = BaseTableManager.get_sqlite_type("metadata", Dict[str, str], _make_field_info())
        assert result == "TEXT"

    def test_list_type(self):
        """list maps to TEXT (JSON)."""
        result = BaseTableManager.get_sqlite_type("tags", list, _make_field_info())
        assert result == "TEXT"

    def test_typed_list_type(self):
        """List[str] maps to TEXT (JSON)."""
        result = BaseTableManager.get_sqlite_type("tags", List[str], _make_field_info())
        assert result == "TEXT"

    def test_bytes_type(self):
        """bytes maps to BLOB NOT NULL."""
        result = BaseTableManager.get_sqlite_type("content", bytes, _make_field_info())
        assert result == "BLOB NOT NULL"

    def test_optional_bytes_type(self):
        """Optional[bytes] maps to BLOB (nullable)."""
        result = BaseTableManager.get_sqlite_type("content", Optional[bytes], _make_field_info())
        assert result == "BLOB"

    def test_enum_type(self):
        """Enum maps to TEXT NOT NULL."""
        result = BaseTableManager.get_sqlite_type("status", SampleEnum, _make_field_info())
        assert result == "TEXT NOT NULL"

    def test_enum_type_with_default(self):
        """Enum with default includes DEFAULT clause."""
        result = BaseTableManager.get_sqlite_type(
            "status", SampleEnum, _make_field_info(default=SampleEnum.ACTIVE)
        )
        assert result == "TEXT NOT NULL DEFAULT 'active'"

    def test_pydantic_model_type(self):
        """Pydantic BaseModel subclass maps to TEXT (JSON)."""
        result = BaseTableManager.get_sqlite_type("config", NestedModel, _make_field_info())
        assert result == "TEXT"

    def test_unknown_type_defaults_to_text(self):
        """Unknown types default to TEXT NOT NULL."""
        result = BaseTableManager.get_sqlite_type("unknown", object, _make_field_info())
        assert result == "TEXT NOT NULL"


# --- check_table_exists_sqlite tests ---

class TestCheckTableExistsSqlite:
    """Tests for check_table_exists_sqlite()."""

    @pytest.fixture
    async def backend(self):
        """Provide an initialized in-memory SQLiteBackend."""
        b = SQLiteBackend(":memory:")
        await b.initialize()
        yield b
        await b.close()

    async def test_table_does_not_exist(self, backend):
        """Returns False for a non-existent table."""
        result = await check_table_exists_sqlite("nonexistent_table", backend)
        assert result is False

    async def test_table_exists_after_creation(self, backend):
        """Returns True after creating the table."""
        await backend.execute_write(
            "CREATE TABLE test_check (id INTEGER PRIMARY KEY, name TEXT)"
        )
        result = await check_table_exists_sqlite("test_check", backend)
        assert result is True

    async def test_views_are_not_tables(self, backend):
        """Views should not be reported as tables."""
        await backend.execute_write(
            "CREATE TABLE base_table (id INTEGER PRIMARY KEY)"
        )
        await backend.execute_write(
            "CREATE VIEW test_view AS SELECT id FROM base_table"
        )
        result = await check_table_exists_sqlite("test_view", backend)
        assert result is False
