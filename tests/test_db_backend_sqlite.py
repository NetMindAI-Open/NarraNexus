"""
@file_name: test_db_backend_sqlite.py
@author: NexusAgent
@date: 2026-04-02
@description: Tests for the SQLiteBackend implementation

Contains two test classes:
1. TestSQLiteBackendContract - runs all contract tests from BackendContractTests
2. TestSQLiteSpecific - tests for SQLite-specific behavior (WAL mode, JSON storage, etc.)
"""

from __future__ import annotations

import asyncio
import json

import pytest

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend

from .test_db_backend_interface import BackendContractTests


# Helper to create the test table used by contract tests
CREATE_TEST_TABLE = """
CREATE TABLE IF NOT EXISTS test_items (
    id TEXT PRIMARY KEY,
    name TEXT,
    value INTEGER,
    data TEXT
)
"""


@pytest.fixture
async def db():
    """Provide an initialized SQLiteBackend with the test_items table."""
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await backend.execute_write(CREATE_TEST_TABLE)
    yield backend
    await backend.close()


class TestSQLiteBackendContract(BackendContractTests):
    """Run all contract tests against the SQLiteBackend (in-memory)."""
    pass


class TestSQLiteSpecific:
    """Tests for SQLite-specific behavior."""

    async def test_placeholder(self, db):
        """SQLiteBackend uses '?' placeholder."""
        assert db.placeholder == "?"

    async def test_dialect(self, db):
        """SQLiteBackend reports 'sqlite' dialect."""
        assert db.dialect == "sqlite"

    async def test_wal_mode(self, sqlite_db_path):
        """File-based SQLite uses WAL journal mode."""
        backend = SQLiteBackend(sqlite_db_path)
        await backend.initialize()
        try:
            rows = await backend.execute("PRAGMA journal_mode")
            assert rows[0]["journal_mode"] == "wal"
        finally:
            await backend.close()

    async def test_foreign_keys_enabled(self, db):
        """Foreign key enforcement is enabled."""
        rows = await db.execute("PRAGMA foreign_keys")
        assert rows[0]["foreign_keys"] == 1

    async def test_json_storage(self, db):
        """Dict and list values are serialized to JSON strings."""
        test_data = {"key": "value", "nested": [1, 2, 3]}
        await db.insert("test_items", {
            "id": "json1",
            "name": "JSON Test",
            "value": 0,
            "data": test_data,
        })

        row = await db.get_one("test_items", {"id": "json1"})
        assert row is not None
        # Stored as JSON string
        parsed = json.loads(row["data"])
        assert parsed == test_data

    async def test_bool_storage(self, db):
        """Boolean values are stored as 0/1 integers."""
        await db.insert("test_items", {"id": "bool1", "name": "Bool", "value": True})
        await db.insert("test_items", {"id": "bool2", "name": "Bool", "value": False})

        row_true = await db.get_one("test_items", {"id": "bool1"})
        row_false = await db.get_one("test_items", {"id": "bool2"})

        assert row_true["value"] == 1
        assert row_false["value"] == 0

    async def test_upsert_conflict_update(self, db):
        """Upsert updates non-id fields on conflict."""
        await db.insert("test_items", {"id": "uc1", "name": "Original", "value": 1, "data": "old"})
        await db.upsert(
            "test_items",
            {"id": "uc1", "name": "Changed", "value": 99, "data": "new"},
            "id",
        )

        row = await db.get_one("test_items", {"id": "uc1"})
        assert row["name"] == "Changed"
        assert row["value"] == 99
        assert row["data"] == "new"

        # Only one row should exist with this id
        all_rows = await db.get("test_items", {"id": "uc1"})
        assert len(all_rows) == 1

    async def test_concurrent_reads_during_write(self, db):
        """Multiple concurrent reads succeed (WAL mode)."""
        # Insert some data first
        for i in range(10):
            await db.insert("test_items", {"id": f"cr_{i}", "name": f"N{i}", "value": i})

        # Run several reads concurrently
        async def read_all():
            return await db.get("test_items")

        results = await asyncio.gather(
            read_all(), read_all(), read_all(), read_all(), read_all()
        )
        for r in results:
            assert len(r) == 10

    async def test_sql_injection_prevention(self, db):
        """Identifiers with invalid characters are rejected."""
        with pytest.raises(ValueError, match="can only contain"):
            await db.get("test_items; DROP TABLE test_items")

        with pytest.raises(ValueError, match="can only contain"):
            await db.insert("test_items", {"id": "ok", "bad col!": "val"})

    async def test_not_initialized_raises(self):
        """Using an uninitialized backend raises RuntimeError."""
        backend = SQLiteBackend(":memory:")
        with pytest.raises(RuntimeError, match="not initialized"):
            await backend.get("test_items")

    async def test_datetime_storage(self, db):
        """datetime values are stored as ISO 8601 strings."""
        from datetime import datetime

        dt = datetime(2026, 4, 2, 12, 30, 45)
        await db.insert("test_items", {"id": "dt1", "name": dt, "value": 0})

        row = await db.get_one("test_items", {"id": "dt1"})
        assert row["name"] == "2026-04-02T12:30:45"
