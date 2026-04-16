"""
@file_name: conftest.py
@author: Bin Liang
@date: 2026-04-16
@description: Shared pytest fixtures for async DB-backed tests.

Provides `db_client`: a fresh in-memory SQLite-backed AsyncDatabaseClient
per test, with all tables from schema_registry auto-migrated.
"""
import pytest_asyncio

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.schema_registry import auto_migrate


@pytest_asyncio.fixture
async def db_client():
    """
    In-memory SQLite AsyncDatabaseClient with all tables migrated.
    Each test gets a fresh instance to prevent row leakage across tests.
    """
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()
