"""
@file_name: conftest.py
@author: Bin Liang
@date: 2026-04-16
@description: Shared pytest fixtures for async DB-backed tests.

Provides `db_client`: a fresh in-memory SQLite-backed AsyncDatabaseClient
per test, with all tables from schema_registry auto-migrated.

Also owns two suite-wide safety nets:

1. `_isolate_shared_db` redirects `settings.database_url` to a per-session
   temp SQLite file, so code paths that lazily acquire the SHARED factory
   client (`db_factory.get_db_client()`, e.g. via
   `XYZBaseModule.get_mcp_db_client()` inside channel/module code under
   test) can never read or write the developer's real database.

2. `pytest_sessionfinish` closes every factory client. The SQLite
   backend's aiosqlite connection runs a NON-daemon worker thread; a
   client leaked by a lazy acquisition (whose per-test event loop is long
   gone by session end) otherwise blocks interpreter shutdown forever —
   the "pytest prints the summary but the process never exits" hang.
"""
import asyncio

import pytest
import pytest_asyncio

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.schema_registry import auto_migrate


@pytest.fixture(scope="session", autouse=True)
def _isolate_shared_db(tmp_path_factory):
    """Point the shared-client factory at a throwaway SQLite file.

    The file is pre-migrated so lazy factory acquisitions see the normal
    schema (empty tables), exactly like a freshly initialized deployment —
    NOT the developer's live database.
    """
    import os

    from xyz_agent_context.settings import settings

    db_path = tmp_path_factory.mktemp("shared_db") / "factory_isolated.db"

    async def _premigrate() -> None:
        backend = SQLiteBackend(str(db_path))
        await backend.initialize()
        await auto_migrate(backend)
        await backend.close()

    asyncio.run(_premigrate())

    original_url = settings.database_url
    original_proxy = os.environ.pop("SQLITE_PROXY_URL", None)
    settings.database_url = f"sqlite:///{db_path}"
    yield
    settings.database_url = original_url
    if original_proxy is not None:
        os.environ["SQLITE_PROXY_URL"] = original_proxy


def pytest_sessionfinish(session, exitstatus):
    """Close leaked factory clients so their worker threads let us exit."""
    from xyz_agent_context.utils.db_factory import close_db_client

    asyncio.run(close_db_client())


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
