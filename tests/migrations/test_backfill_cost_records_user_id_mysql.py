"""
@file_name: test_backfill_cost_records_user_id_mysql.py
@author: Bin Liang
@date: 2026-07-22
@description: MySQL-side verification for scripts/backfill_cost_records_user_id.py.

The backfill ships THREE hand-written raw SQL statements (_COUNT_FILLABLE /
_COUNT_ORPHAN / _BACKFILL) that bypass the AsyncDatabaseClient helpers, and its
ONLY real use is against MySQL on the host. Dialect faults (1064 parse errors,
correlated-subquery UPDATE semantics, EXISTS behavior) surface only when the
real engine parses them — SQLite in the unit test cannot catch these. This is
the project's dual-dialect hard rule; the SQLite test is not a substitute.

Enable by setting NARRANEXUS_MYSQL_TEST_URL to a throwaway MySQL DSN:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"

Assertions target only the rows this test seeds (the DB is shared, so global
counts are not isolated) — enough to prove the SQL parses AND does the right
thing on MySQL.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import pytest_asyncio

from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.db_backend_mysql import MySQLBackend
from xyz_agent_context.utils.schema_registry import auto_migrate


MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"

pytestmark = pytest.mark.skipif(
    not os.environ.get(MYSQL_URL_ENV),
    reason=(
        f"{MYSQL_URL_ENV} not set. The backfill's raw SQL must be parsed by a "
        f"real MySQL; SQLite cannot catch dialect faults. Example DSN: "
        f"mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
    ),
)

# Load the script module to reuse its exact SQL constants.
_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "backfill_cost_records_user_id.py"
)
_spec = importlib.util.spec_from_file_location("backfill_cost_records_user_id", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

# Deterministic ids so reruns are idempotent on a shared DB.
_LIVE_AGENT = "a_bf_live_mysql"
_DEAD_AGENT = "a_bf_dead_mysql"
_OWNER = "usr_bf_live_mysql"


def _parse_mysql_url(url: str) -> dict:
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


async def _cleanup(client) -> None:
    for aid in (_LIVE_AGENT, _DEAD_AGENT):
        await client.execute(
            "DELETE FROM cost_records WHERE agent_id = %s", params=(aid,), fetch=False
        )
    await client.execute(
        "DELETE FROM agents WHERE agent_id = %s", params=(_LIVE_AGENT,), fetch=False
    )


@pytest_asyncio.fixture
async def mysql_client():
    cfg = _parse_mysql_url(os.environ[MYSQL_URL_ENV])
    backend = MySQLBackend(cfg)
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    await _cleanup(client)
    yield client
    await _cleanup(client)
    await client.close()


@pytest.mark.asyncio
async def test_backfill_sql_parses_and_backfills_on_mysql(mysql_client):
    client = mysql_client
    await client.insert("agents", {
        "agent_id": _LIVE_AGENT, "agent_name": "Live", "created_by": _OWNER,
    })
    live_id = await client.insert("cost_records", {
        "agent_id": _LIVE_AGENT, "call_type": "agent_loop", "model": "claude-x",
    })
    dead_id = await client.insert("cost_records", {
        "agent_id": _DEAD_AGENT, "call_type": "agent_loop", "model": "claude-x",
    })

    # The count queries must parse and execute on MySQL (this test's rows alone
    # already guarantee >= 1 each; other rows in the shared DB may add more).
    fillable = await client.execute(bf._COUNT_FILLABLE, params=(), fetch=True)
    orphan = await client.execute(bf._COUNT_ORPHAN, params=(), fetch=True)
    assert fillable[0]["n"] >= 1
    assert orphan[0]["n"] >= 1

    # The correlated-subquery UPDATE must parse and fill only the live row.
    await client.execute(bf._BACKFILL, params=(), fetch=False)

    live = await client.execute(
        "SELECT user_id FROM cost_records WHERE id = %s", params=(live_id,), fetch=True
    )
    dead = await client.execute(
        "SELECT user_id FROM cost_records WHERE id = %s", params=(dead_id,), fetch=True
    )
    assert live[0]["user_id"] == _OWNER
    assert dead[0]["user_id"] is None

    # Idempotent: a second run leaves the orphan NULL and the live row filled.
    await client.execute(bf._BACKFILL, params=(), fetch=False)
    dead2 = await client.execute(
        "SELECT user_id FROM cost_records WHERE id = %s", params=(dead_id,), fetch=True
    )
    assert dead2[0]["user_id"] is None
