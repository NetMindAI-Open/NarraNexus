"""
@file_name: test_agent_rename_rowcount_mysql.py
@author: NarraNexus
@date: 2026-08-18
@description: The rename bug's mechanism, against the dialect it only occurs on.

`AgentRepository.update_agent` returns `cursor.rowcount`, and that number means
two different things: MATCHED rows on SQLite, CHANGED rows on MySQL. The route
used to judge success by `affected_rows > 0`, so re-saving a value the row
already held answered `success=False, error="No changes made"` **on cloud
only** — for a row holding exactly what the caller asked for. The user reads
"save failed", retries, and every retry answers the same way while the database
has had the new name all along. That is the Shenzhen round-2 P1 report.

Every other test of this fix has to FAKE the MySQL reading of rowcount
(monkeypatching update_agent to return 0), because the SQLite fixture returns 1
for a no-op write and would pass vacuously on the one dialect where the bug
cannot happen. This file is the twin that needs no faking: it asserts the
asymmetry itself, and then that the route is right anyway.

Until CI grew a MySQL service (2026-08-17), this could only be verified on a
laptop with a container up — which is why the fix shipped with its live-test
box unchecked. Run it the same way locally:

    docker run --rm -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root \\
        -e MYSQL_DATABASE=nxtest --name nx-mysql-test mysql:8
    export NARRANEXUS_MYSQL_TEST_URL=mysql://root:root@127.0.0.1:3306/nxtest
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import backend.routes.auth as auth_mod
from narranexus.platform.repository import AgentRepository
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

from tests.mysql_dialect import (
    mysql_configured,
    mysql_url,
    parse_mysql_url,
    skip_reason,
)

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "the MATCHED-vs-CHANGED rowcount asymmetry that made a landed rename "
        "report failure on cloud and nowhere else"
    ),
)

_PREFIX = "mysqlrename"
AGENT = f"agent_{_PREFIX}_1"
OWNER = f"usr_{_PREFIX}"

_SCOPED = (("agents", "agent_id"), ("users", "user_id"))


async def _clean(client, *, quiet: bool) -> None:
    """Remove this file's rows. `quiet` only for teardown — see the convention
    in test_team_room_activity_mysql.py: leftovers colliding mid-test read like
    the dialect bug this file exists to detect."""
    for table, col in _SCOPED:
        try:
            await client.execute(
                f"DELETE FROM {table} WHERE {col} LIKE %s",
                (f"%{_PREFIX}%",),
                fetch=False,
            )
        except Exception:  # noqa: BLE001 — see `quiet`
            if not quiet:
                raise


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    await _clean(client, quiet=False)
    yield client
    await _clean(client, quiet=True)
    await client.close()


@pytest_asyncio.fixture
async def seeded(mysql_client):
    await mysql_client.insert(
        "users", {"user_id": OWNER, "user_type": "individual", "role": "user"}
    )
    await AgentRepository(mysql_client).add_agent(
        agent_id=AGENT, agent_name="小绿", created_by=OWNER
    )
    return mysql_client


@pytest.mark.asyncio
async def test_mysql_reports_zero_rows_for_a_no_op_update(seeded):
    """The asymmetry itself — the fact the whole fix is about.

    If this ever returns 1, MySQL has been given CLIENT_FOUND_ROWS somewhere
    and the premise of the fix changed; the fix stays correct either way (it
    ignores rowcount), but the reasoning recorded around it would be stale.
    """
    repo = AgentRepository(seeded)
    assert await repo.update_agent(AGENT, {"agent_name": "小蓝"}) == 1
    assert await repo.update_agent(AGENT, {"agent_name": "小蓝"}) == 0, (
        "MySQL counted a no-op write as a changed row — the MATCHED-vs-CHANGED "
        "asymmetry this fix is built on no longer holds"
    )


def _build_app(db, monkeypatch) -> FastAPI:
    """The route, wired to the real MySQL client.

    `get_db_client` is patched rather than overridden as a dependency because
    the handler awaits it in its body, not through `Depends` — and through
    `monkeypatch`, so the MySQL client cannot leak into a later test that
    expects the SQLite fixture.
    """
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        # The ownership check reads `http_request.state.user_id`, which only a
        # middleware can put there on a real request.
        request.state.user_id = OWNER
        return await call_next(request)

    async def _db():
        return db

    monkeypatch.setattr(auth_mod, "get_db_client", _db)
    return app


def _async_client(app: FastAPI) -> httpx.AsyncClient:
    """`httpx.AsyncClient` + `ASGITransport`, not `TestClient` — the pooled
    aiomysql connection must not be touched from a foreign event loop.

    An equivalent implementation, with the cross-loop mechanism spelled out in
    full, lives in `tests/backend/test_agents_bus_failures_mysql.py`. Two copies
    of a four-line idiom that ~14 test files already use is not worth an
    abstraction, and hoisting it into `tests/mysql_dialect.py` would give a
    helper every twin imports two new top-level dependencies. Read that one for
    the why; this is the same problem with the same answer. (A worse answer came
    first here: dropping the HTTP layer, which bought a single loop at the cost
    of the serialization this row's only real-world defect was reported
    through.)
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest_asyncio.fixture
async def client(seeded, monkeypatch):
    """One app and one client per test.

    Function-scoped on purpose: `monkeypatch` is, so a longer-lived app would
    outlive the `get_db_client` patch and start answering from the real
    factory mid-test.
    """
    async with _async_client(_build_app(seeded, monkeypatch)) as c:
        yield c


async def _put(client, payload: dict) -> dict:
    res = await client.put(f"/api/auth/agents/{AGENT}", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_re_saving_the_stored_name_succeeds_on_mysql(seeded, client):
    """The user-visible half, on the dialect where it used to fail.

    Before the fix this answered `success=False, error="No changes made"` here
    and success on SQLite — the entire Shenzhen P1 symptom. Driven over HTTP so
    the body parsing and `response_model` serialization the reporter actually
    saw are part of what is verified.
    """
    body = await _put(client, {"agent_name": "小绿"})
    assert body["success"] is True, body.get("error")
    assert body["agent"]["name"] == "小绿"

    stored = await AgentRepository(seeded).get_agent(AGENT)
    assert stored.agent_name == "小绿"


@pytest.mark.asyncio
async def test_a_real_rename_still_persists_on_mysql(seeded, client):
    body = await _put(client, {"agent_name": "小蓝"})
    assert body["success"] is True, body.get("error")

    stored = await AgentRepository(seeded).get_agent(AGENT)
    assert stored.agent_name == "小蓝"


@pytest.mark.asyncio
async def test_the_visibility_flag_round_trips_through_the_real_boolean_column(
    seeded, client
):
    """`is_public` is the one field whose STORED representation differs by
    dialect — TINYINT on MySQL, INTEGER on SQLite, and `_row_to_entity` may
    hand back either a bool or an int. That is why `agent_field_matches`
    dispatches it separately, and until now the reasoning was only on paper.

    The second PUT is this PR's subject in boolean form: an unchanged value,
    zero changed rows on MySQL, and still a success.
    """
    assert (await AgentRepository(seeded).get_agent(AGENT)).is_public is False

    body = await _put(client, {"is_public": True})
    assert body["success"] is True, body.get("error")
    stored = await AgentRepository(seeded).get_agent(AGENT)
    # `is True`, not `== 1`: the point is that the round trip lands on a bool.
    assert stored.is_public is True

    again = await _put(client, {"is_public": True})
    assert again["success"] is True, again.get("error")
    assert (await AgentRepository(seeded).get_agent(AGENT)).is_public is True
