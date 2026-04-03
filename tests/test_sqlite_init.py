"""
@file_name: test_sqlite_init.py
@author: NexusAgent
@date: 2026-04-02
@description: Tests for init_local_database() and create_all_tables_sqlite()

Verifies that:
1. init_local_database() creates all tables in a temp SQLite file
2. Every expected table exists in sqlite_master
3. Basic insert/read works for the agents table
4. JSON-field insert/read works for the narratives table
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import pytest_asyncio

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database_table_management.create_all_tables_sqlite import (
    ALL_TABLE_NAMES,
    create_all_tables_sqlite,
    init_local_database,
)
from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
    BUS_TABLE_NAMES,
)


@pytest.fixture
def tmp_db_path(tmp_path):
    """Return a temporary database file path."""
    return str(tmp_path / "test_init.db")


@pytest.mark.asyncio
async def test_init_local_database_creates_all_tables(tmp_db_path: str):
    """init_local_database should create all core + bus tables."""
    await init_local_database(tmp_db_path)

    # Re-open to verify
    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        rows = await backend.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {row["name"] for row in rows}

        expected = set(ALL_TABLE_NAMES) | set(BUS_TABLE_NAMES)
        missing = expected - table_names
        assert not missing, f"Missing tables: {missing}"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_each_table_exists_in_sqlite_master(tmp_db_path: str):
    """Every table in ALL_TABLE_NAMES should be present after creation."""
    await init_local_database(tmp_db_path)

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        for table_name in ALL_TABLE_NAMES:
            rows = await backend.execute(
                "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            assert rows[0]["cnt"] == 1, f"Table '{table_name}' not found in sqlite_master"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_insert_and_read_agents_table(tmp_db_path: str):
    """Should be able to insert a row into agents and read it back."""
    await init_local_database(tmp_db_path)

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        # Insert
        await backend.insert("agents", {
            "agent_id": "agt_test001",
            "agent_name": "Test Agent",
            "created_by": "user_001",
            "agent_description": "A test agent",
            "agent_type": "assistant",
            "is_public": 0,
        })

        # Read back
        row = await backend.get_one("agents", {"agent_id": "agt_test001"})
        assert row is not None
        assert row["agent_id"] == "agt_test001"
        assert row["agent_name"] == "Test Agent"
        assert row["created_by"] == "user_001"
        assert row["is_public"] == 0
        assert row["id"] == 1  # auto-increment
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_insert_and_read_narratives_with_json(tmp_db_path: str):
    """Should be able to insert narratives with JSON fields and read them back."""
    await init_local_database(tmp_db_path)

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        narrative_info = {
            "name": "Test Narrative",
            "description": "A test narrative",
            "current_summary": "Summary here",
            "actors": [{"id": "user_001", "type": "user"}],
        }
        event_ids = ["evt_aaa", "evt_bbb"]

        await backend.insert("narratives", {
            "narrative_id": "nar_test001",
            "type": "chat",
            "agent_id": "agt_test001",
            "narrative_info": json.dumps(narrative_info),
            "event_ids": json.dumps(event_ids),
            "topic_keywords": json.dumps(["greeting", "intro"]),
            "is_special": "other",
        })

        # Read back
        row = await backend.get_one("narratives", {"narrative_id": "nar_test001"})
        assert row is not None
        assert row["narrative_id"] == "nar_test001"
        assert row["type"] == "chat"
        assert row["agent_id"] == "agt_test001"

        # Verify JSON fields round-trip
        info = json.loads(row["narrative_info"])
        assert info["name"] == "Test Narrative"
        assert len(info["actors"]) == 1

        ids = json.loads(row["event_ids"])
        assert ids == ["evt_aaa", "evt_bbb"]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_idempotent_creation(tmp_db_path: str):
    """Calling init_local_database twice should not fail (IF NOT EXISTS)."""
    await init_local_database(tmp_db_path)
    # Second call should succeed without error
    await init_local_database(tmp_db_path)

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        rows = await backend.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {row["name"] for row in rows}
        expected = set(ALL_TABLE_NAMES) | set(BUS_TABLE_NAMES)
        missing = expected - table_names
        assert not missing, f"Missing tables after second init: {missing}"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_bus_tables_created(tmp_db_path: str):
    """MessageBus tables should be created by init_local_database."""
    await init_local_database(tmp_db_path)

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        for table_name in BUS_TABLE_NAMES:
            rows = await backend.execute(
                "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            assert rows[0]["cnt"] == 1, f"Bus table '{table_name}' not found"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_indexes_created(tmp_db_path: str):
    """Key indexes should be present after initialization."""
    await init_local_database(tmp_db_path)

    backend = SQLiteBackend(tmp_db_path)
    await backend.initialize()
    try:
        rows = await backend.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' OR name LIKE 'uk_%'"
        )
        index_names = {row["name"] for row in rows}
        # Spot-check a few important indexes
        assert "idx_agents_agent_id" in index_names
        assert "idx_events_event_id" in index_names
        assert "idx_narratives_narrative_id" in index_names
        assert "idx_module_instances_instance_id" in index_names
        assert "uk_instance_narrative" in index_names
    finally:
        await backend.close()
