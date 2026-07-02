"""
@file_name: test_agents_bus_failures_mysql.py
@author: Bin Liang
@date: 2026-07-02
@description: Real-MySQL regression coverage for the two hand-written raw
SQL statements in `backend/routes/agents_bus_failures.py`.

Why this file exists
---------------------
`tests/backend/test_agents_bus_failures_routes.py` only exercises these
queries against `SQLiteBackend(":memory:")`. Per project policy, any new
hand-written raw SQL file must also be validated against a real MySQL
backend (dialect drift like `"..."`-quoted identifiers or bad placeholder
usage passes silently on SQLite and only 1064s on MySQL — see
`local_bus.py`'s mirror md for a prior incident of exactly this class).

Both queries here already use bare identifiers + `%s` placeholders, so this
is a belt-and-suspenders regression guard rather than a known-bug fix (unlike
`test_quota_repository_mysql_underflow.py`, which reproduces a real prod
incident) — but the two statements (a plain `SELECT ... LIMIT 1` and a
two-table `JOIN` with an `ORDER BY` on an aliased column) are exactly the
shapes that have broken before in this codebase when only tested on SQLite.

Enable by setting `NARRANEXUS_MYSQL_TEST_URL` to a DSN pointing at a
throwaway MySQL, same convention as `test_quota_repository_mysql_underflow.py`:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
"""
from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.db_backend_mysql import MySQLBackend
from xyz_agent_context.utils.schema_registry import auto_migrate

import backend.routes.agents_bus_failures as bus_failures_mod


MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"


def _parse_mysql_url(url: str) -> dict:
    # Same tiny inline parser as test_quota_repository_mysql_underflow.py —
    # no shared helper exists yet in this codebase for a single DSN format.
    assert url.startswith("mysql://"), f"expected mysql://..., got {url!r}"
    body = url[len("mysql://") :]
    creds, _, host_db = body.partition("@")
    user, _, password = creds.partition(":")
    host_port, _, database = host_db.partition("/")
    host, _, port = host_port.partition(":")
    return {
        "host": host,
        "port": int(port) if port else 3306,
        "user": user,
        "password": password,
        "database": database,
    }


pytestmark = pytest.mark.skipif(
    not os.environ.get(MYSQL_URL_ENV),
    reason=(
        f"{MYSQL_URL_ENV} not set. These tests validate agents_bus_failures.py's "
        f"raw SQL against a real MySQL dialect (bare identifiers / %s "
        f"placeholders can silently pass on SQLite and 1064 on MySQL). "
        f"Example DSN: mysql://root:xyz_root_pass@127.0.0.1:3306/"
        f"xyz_agent_context"
    ),
)


@pytest_asyncio.fixture
async def mysql_client():
    cfg = _parse_mysql_url(os.environ[MYSQL_URL_ENV])
    backend = MySQLBackend(cfg)
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    # Deterministic ids, cleared up-front so reruns are idempotent even
    # though the DB is shared.
    await client.execute(
        "DELETE FROM bus_message_failures WHERE agent_id IN (%s, %s)",
        params=("mysql_agent_a", "mysql_agent_ghost"),
        fetch=False,
    )
    await client.execute(
        "DELETE FROM bus_messages WHERE message_id IN (%s)",
        params=("mysql_m1",),
        fetch=False,
    )
    await client.execute(
        "DELETE FROM agents WHERE agent_id = %s",
        params=("mysql_agent_a",),
        fetch=False,
    )

    yield client

    await client.close()


def _build_app(db_client, viewer_id: str = "mysql_user_x") -> FastAPI:
    app = FastAPI()
    app.include_router(bus_failures_mod.router, prefix="/api/agents")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        return await call_next(request)

    async def _get_db_override():
        return db_client

    bus_failures_mod.get_db_client = _get_db_override
    return app


def _async_client(app: FastAPI) -> httpx.AsyncClient:
    """`httpx.AsyncClient` + `ASGITransport`, NOT `fastapi.testclient.TestClient`.

    `TestClient` drives the ASGI app from a background thread with its own
    event loop (via anyio's blocking portal). The aiomysql connection pool
    behind `mysql_client` is bound to the CURRENT (pytest-asyncio) loop —
    crossing loops mid-request raises
    ``RuntimeError: ... got Future ... attached to a different loop``
    inside `aiomysql.Pool.release`. `ASGITransport` runs the app in-process
    on the same loop as the test coroutine, so the pooled connection is
    never touched from a foreign loop.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _seed_agent(db_client, agent_id="mysql_agent_a", owner="mysql_user_x"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


async def _seed_failure(
    db_client,
    message_id="mysql_m1",
    agent_id="mysql_agent_a",
    channel_id="ch1",
    retry_count=3,
    error="OpenAI API key invalid",
):
    await db_client.insert(
        "bus_messages",
        {
            "message_id": message_id,
            "channel_id": channel_id,
            "from_agent": "peer",
            "content": "hello",
            "created_at": "2026-07-01T00:00:00+00:00",
        },
    )
    await db_client.insert(
        "bus_message_failures",
        {
            "message_id": message_id,
            "agent_id": agent_id,
            "retry_count": retry_count,
            "last_error": error,
            "last_retry_at": "2026-07-01T00:05:00+00:00",
        },
    )


@pytest.fixture(autouse=True)
def _restore_get_db():
    original = bus_failures_mod.get_db_client
    yield
    bus_failures_mod.get_db_client = original


@pytest.mark.asyncio
async def test_list_query_runs_on_real_mysql(mysql_client):
    """The JOIN + ORDER BY query (`list_bus_failures`) must actually execute
    on MySQL, not just parse cleanly against SQLite's laxer dialect."""
    await _seed_agent(mysql_client)
    await _seed_failure(mysql_client)
    app = _build_app(mysql_client)

    async with _async_client(app) as client:
        r = await client.get("/api/agents/mysql_agent_a/bus-failures")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert len(body["failures"]) == 1
    failure = body["failures"][0]
    assert failure["message_id"] == "mysql_m1"
    assert failure["channel_id"] == "ch1"
    assert failure["retry_count"] == 3


@pytest.mark.asyncio
async def test_owner_lookup_query_runs_on_real_mysql(mysql_client):
    """The `SELECT created_by FROM agents WHERE agent_id=%s LIMIT 1`
    ownership check (`_require_owned_agent`) must run on MySQL too."""
    await _seed_agent(mysql_client)
    app = _build_app(mysql_client)

    async with _async_client(app) as client:
        # Owned agent → 200.
        r_owned = await client.get("/api/agents/mysql_agent_a/bus-failures")
        assert r_owned.status_code == 200, r_owned.text

        # Unknown agent → the ownership SELECT returns no rows → 404.
        r_missing = await client.get("/api/agents/mysql_agent_ghost/bus-failures")
        assert r_missing.status_code == 404
