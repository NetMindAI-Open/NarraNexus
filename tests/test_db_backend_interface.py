"""
@file_name: test_db_backend_interface.py
@author: NexusAgent
@date: 2026-04-02
@description: Contract tests for the DatabaseBackend interface (mixin pattern)

This module defines BackendContractTests as a mixin class containing async test
methods for all CRUD operations, upsert, get_by_ids, pagination, and transactions.
Backend-specific test classes inherit from this mixin to verify their implementation
conforms to the DatabaseBackend contract.

Subclasses must define a `db` fixture that returns an initialized DatabaseBackend
instance with a 'test_items' table already created:

    CREATE TABLE test_items (
        id TEXT PRIMARY KEY,
        name TEXT,
        value INTEGER,
        data TEXT
    )
"""

from __future__ import annotations

import pytest


class BackendContractTests:
    """
    Mixin class providing contract tests for DatabaseBackend implementations.

    Subclasses must provide a `db` fixture that yields an initialized backend
    with the test_items table created.
    """

    # ===== Insert Tests =====

    async def test_insert_and_get_one(self, db):
        """Insert a row and retrieve it by filter."""
        await db.insert("test_items", {"id": "item_1", "name": "Alpha", "value": 10})
        row = await db.get_one("test_items", {"id": "item_1"})

        assert row is not None
        assert row["id"] == "item_1"
        assert row["name"] == "Alpha"
        assert row["value"] == 10

    async def test_insert_empty_data_raises(self, db):
        """Inserting empty data should raise ValueError."""
        with pytest.raises(ValueError):
            await db.insert("test_items", {})

    # ===== Get Tests =====

    async def test_get_all(self, db):
        """Get all rows without filters."""
        await db.insert("test_items", {"id": "a", "name": "A", "value": 1})
        await db.insert("test_items", {"id": "b", "name": "B", "value": 2})

        rows = await db.get("test_items")
        assert len(rows) == 2

    async def test_get_with_filters(self, db):
        """Get rows matching a filter."""
        await db.insert("test_items", {"id": "a", "name": "A", "value": 1})
        await db.insert("test_items", {"id": "b", "name": "B", "value": 2})

        rows = await db.get("test_items", {"name": "B"})
        assert len(rows) == 1
        assert rows[0]["id"] == "b"

    async def test_get_with_fields(self, db):
        """Get only specific fields."""
        await db.insert("test_items", {"id": "a", "name": "A", "value": 42})

        rows = await db.get("test_items", fields=["id", "value"])
        assert len(rows) == 1
        assert "id" in rows[0]
        assert "value" in rows[0]
        assert "name" not in rows[0]

    async def test_get_one_returns_none_when_missing(self, db):
        """get_one returns None when no row matches."""
        row = await db.get_one("test_items", {"id": "nonexistent"})
        assert row is None

    # ===== Pagination Tests =====

    async def test_get_with_limit(self, db):
        """Limit the number of returned rows."""
        for i in range(5):
            await db.insert("test_items", {"id": f"item_{i}", "name": f"N{i}", "value": i})

        rows = await db.get("test_items", limit=3)
        assert len(rows) == 3

    async def test_get_with_offset(self, db):
        """Skip rows with offset."""
        for i in range(5):
            await db.insert("test_items", {"id": f"item_{i}", "name": f"N{i}", "value": i})

        rows = await db.get("test_items", limit=2, offset=3, order_by="value ASC")
        assert len(rows) == 2
        assert rows[0]["value"] == 3
        assert rows[1]["value"] == 4

    async def test_get_with_order_by(self, db):
        """Results are sorted by order_by."""
        await db.insert("test_items", {"id": "c", "name": "C", "value": 30})
        await db.insert("test_items", {"id": "a", "name": "A", "value": 10})
        await db.insert("test_items", {"id": "b", "name": "B", "value": 20})

        rows = await db.get("test_items", order_by="value ASC")
        values = [r["value"] for r in rows]
        assert values == [10, 20, 30]

        rows_desc = await db.get("test_items", order_by="value DESC")
        values_desc = [r["value"] for r in rows_desc]
        assert values_desc == [30, 20, 10]

    # ===== Update Tests =====

    async def test_update(self, db):
        """Update a row and verify the change."""
        await db.insert("test_items", {"id": "u1", "name": "Old", "value": 1})
        count = await db.update("test_items", {"id": "u1"}, {"name": "New", "value": 99})

        assert count == 1
        row = await db.get_one("test_items", {"id": "u1"})
        assert row["name"] == "New"
        assert row["value"] == 99

    async def test_update_empty_data_raises(self, db):
        """Updating with empty data should raise ValueError."""
        with pytest.raises(ValueError):
            await db.update("test_items", {"id": "x"}, {})

    async def test_update_empty_filters_raises(self, db):
        """Updating without filters should raise ValueError."""
        with pytest.raises(ValueError):
            await db.update("test_items", {}, {"name": "X"})

    async def test_update_nonexistent_returns_zero(self, db):
        """Updating a nonexistent row returns 0."""
        count = await db.update("test_items", {"id": "missing"}, {"name": "X"})
        assert count == 0

    # ===== Delete Tests =====

    async def test_delete(self, db):
        """Delete a row and confirm it's gone."""
        await db.insert("test_items", {"id": "d1", "name": "Del", "value": 0})
        count = await db.delete("test_items", {"id": "d1"})

        assert count == 1
        row = await db.get_one("test_items", {"id": "d1"})
        assert row is None

    async def test_delete_empty_filters_raises(self, db):
        """Deleting without filters should raise ValueError."""
        with pytest.raises(ValueError):
            await db.delete("test_items", {})

    async def test_delete_nonexistent_returns_zero(self, db):
        """Deleting a nonexistent row returns 0."""
        count = await db.delete("test_items", {"id": "missing"})
        assert count == 0

    # ===== Upsert Tests =====

    async def test_upsert_insert(self, db):
        """Upsert inserts a new row when the ID does not exist."""
        await db.upsert("test_items", {"id": "up1", "name": "New", "value": 10}, "id")
        row = await db.get_one("test_items", {"id": "up1"})

        assert row is not None
        assert row["name"] == "New"
        assert row["value"] == 10

    async def test_upsert_update(self, db):
        """Upsert updates an existing row when the ID conflicts."""
        await db.insert("test_items", {"id": "up2", "name": "Old", "value": 1})
        await db.upsert("test_items", {"id": "up2", "name": "Updated", "value": 99}, "id")

        row = await db.get_one("test_items", {"id": "up2"})
        assert row["name"] == "Updated"
        assert row["value"] == 99

    async def test_upsert_empty_data_raises(self, db):
        """Upsert with empty data should raise ValueError."""
        with pytest.raises(ValueError):
            await db.upsert("test_items", {}, "id")

    # ===== get_by_ids Tests =====

    async def test_get_by_ids(self, db):
        """Batch-fetch rows by IDs, preserving order."""
        await db.insert("test_items", {"id": "g1", "name": "G1", "value": 1})
        await db.insert("test_items", {"id": "g2", "name": "G2", "value": 2})
        await db.insert("test_items", {"id": "g3", "name": "G3", "value": 3})

        results = await db.get_by_ids("test_items", "id", ["g3", "g1", "g2"])
        assert len(results) == 3
        assert results[0]["id"] == "g3"
        assert results[1]["id"] == "g1"
        assert results[2]["id"] == "g2"

    async def test_get_by_ids_with_missing(self, db):
        """get_by_ids returns None for missing IDs."""
        await db.insert("test_items", {"id": "exist", "name": "E", "value": 1})

        results = await db.get_by_ids("test_items", "id", ["exist", "missing"])
        assert len(results) == 2
        assert results[0] is not None
        assert results[0]["id"] == "exist"
        assert results[1] is None

    async def test_get_by_ids_empty_list(self, db):
        """get_by_ids with empty list returns empty list."""
        results = await db.get_by_ids("test_items", "id", [])
        assert results == []

    async def test_get_by_ids_with_duplicates(self, db):
        """get_by_ids with duplicate IDs returns one result per input ID."""
        await db.insert("test_items", {"id": "dup", "name": "D", "value": 1})

        results = await db.get_by_ids("test_items", "id", ["dup", "dup"])
        assert len(results) == 2
        assert results[0]["id"] == "dup"
        assert results[1]["id"] == "dup"

    # ===== Transaction Tests =====

    async def test_transaction_commit(self, db):
        """Committed transactions persist data."""
        await db.begin_transaction()
        await db.insert("test_items", {"id": "tx1", "name": "TX", "value": 1})
        await db.commit()

        row = await db.get_one("test_items", {"id": "tx1"})
        assert row is not None
        assert row["name"] == "TX"

    async def test_transaction_rollback(self, db):
        """Rolled-back transactions discard data."""
        await db.begin_transaction()
        await db.insert("test_items", {"id": "tx2", "name": "TX", "value": 1})
        await db.rollback()

        row = await db.get_one("test_items", {"id": "tx2"})
        assert row is None

    async def test_double_begin_raises(self, db):
        """Starting a transaction twice should raise RuntimeError."""
        await db.begin_transaction()
        with pytest.raises(RuntimeError):
            await db.begin_transaction()
        await db.rollback()  # clean up

    async def test_commit_without_transaction_raises(self, db):
        """Committing without an active transaction should raise RuntimeError."""
        with pytest.raises(RuntimeError):
            await db.commit()

    async def test_rollback_without_transaction_raises(self, db):
        """Rolling back without an active transaction should raise RuntimeError."""
        with pytest.raises(RuntimeError):
            await db.rollback()

    # ===== Raw Execute Tests =====

    async def test_execute_raw_select(self, db):
        """execute() can run raw SELECT queries."""
        await db.insert("test_items", {"id": "raw1", "name": "Raw", "value": 42})

        ph = db.placeholder
        rows = await db.execute(
            f'SELECT "id", "name" FROM "test_items" WHERE "id" = {ph}',
            ("raw1",),
        )
        assert len(rows) == 1
        assert rows[0]["id"] == "raw1"

    async def test_execute_write_raw(self, db):
        """execute_write() returns affected row count."""
        await db.insert("test_items", {"id": "rw1", "name": "RW", "value": 1})

        ph = db.placeholder
        count = await db.execute_write(
            f'UPDATE "test_items" SET "value" = 999 WHERE "id" = {ph}',
            ("rw1",),
        )
        assert count == 1

        row = await db.get_one("test_items", {"id": "rw1"})
        assert row["value"] == 999
