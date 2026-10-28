"""
@file_name: test_cascade_stop_mysql.py
@author:
@date: 2026-08-07
@description: Real-MySQL coverage for the cascade-stop raw SQL.

Why this file exists
---------------------
Two hand-written statements ship with cascade stop, and both are only
exercised against `SQLiteBackend(":memory:")` elsewhere:

  * `CancelWatcher.poll_once` — `SELECT ... WHERE event_id IN (%s, %s, ...)`,
    a generated placeholder list.
  * `LocalMessageBus.get_pending_messages` — a JOIN plus a correlated
    `NOT EXISTS` subquery. This one is the higher risk of the two: it runs on
    the RAW backend, which does NOT translate `%s` → `?`, so a dialect slip
    here passes on SQLite and 1064s on MySQL.

Project policy for new raw SQL (see `test_agents_bus_failures_mysql.py` and
`test_quota_repository_mysql_underflow.py`): validate against a real MySQL.
`test_trigger_reserved_word_sql.py` records what the alternative looks like —
green on SQLite, 1064 in prod, swallowed by a bare except for two days.

Enable by setting `NARRANEXUS_MYSQL_TEST_URL` to a throwaway MySQL DSN:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
import pytest_asyncio

from narranexus.platform.agent_runtime.cancel_watcher import CancelWatcher
from narranexus.platform.agent_runtime.cancellation import CancellationToken
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from narranexus.platform.utils.timezone import utc_now

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"


def _parse_mysql_url(url: str) -> dict:
    # Same tiny inline parser as the other *_mysql.py files — no shared helper
    # for this DSN format exists in the codebase yet.
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
        f"{MYSQL_URL_ENV} not set. These tests validate the cascade-stop raw "
        f"SQL against a real MySQL dialect (a generated IN-list and a "
        f"correlated NOT EXISTS on the RAW backend, which does no %s→? "
        f"translation). Example DSN: "
        f"mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
    ),
)

_PREFIX = "mysqlcasc"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(_parse_mysql_url(os.environ[MYSQL_URL_ENV]))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    # Shared throwaway DB: clean only what this file created.
    for table, col in (
        ("bus_messages", "message_id"),
        ("events", "event_id"),
        ("bus_channel_members", "channel_id"),
        ("bus_channels", "channel_id"),
        ("agents", "agent_id"),
    ):
        try:
            await client.execute(
                f"DELETE FROM {table} WHERE {col} LIKE %s", (f"{_PREFIX}%",), fetch=False
            )
        except Exception:  # noqa: BLE001 — teardown must not mask a failure
            pass
    await client.close()


async def _seed_run(db, event_id, *, root, state="running", stopped=False):
    """Seed one run. ``stopped=True`` raises the flag AFTER the run started.

    The order matters and is easy to get backwards: the watcher only honours a
    request newer than ``started_at`` (a stale flag must not kill a successor).
    Deriving both stamps here, rather than taking a caller-supplied timestamp
    that was evaluated before this call, keeps the fixture from accidentally
    testing the stale-flag path while claiming to test the live one.
    """
    started = utc_now() - timedelta(seconds=30)
    await db.insert(
        "events",
        {
            "event_id": event_id,
            "trigger": "message_bus",
            "trigger_source": "message_bus",
            "agent_id": f"{_PREFIX}_agent_a",
            "user_id": "u",
            "state": state,
            "started_at": started,
            "last_event_at": started,
            "root_run_id": root,
            "cancel_requested_at": utc_now() if stopped else None,
        },
    )


@pytest.mark.asyncio
async def test_watcher_in_list_runs_on_mysql(mysql_client):
    """The generated `IN (%s, %s, ...)` list executes and reads back."""
    await _seed_run(mysql_client, f"{_PREFIX}_w1", root=f"{_PREFIX}_w1")
    await _seed_run(mysql_client, f"{_PREFIX}_w2", root=f"{_PREFIX}_w2", stopped=True)

    watcher = CancelWatcher(mysql_client, poll_interval_s=0.01)
    quiet, stopped = CancellationToken(), CancellationToken()
    watcher.register(f"{_PREFIX}_w1", quiet)
    watcher.register(f"{_PREFIX}_w2", stopped)

    fired = await watcher.poll_once()

    assert fired == 1
    assert stopped.is_cancelled
    assert not quiet.is_cancelled


@pytest.mark.asyncio
async def test_pending_not_exists_subquery_runs_on_mysql(mysql_client):
    """The JOIN + correlated NOT EXISTS behaves identically to SQLite.

    Same three cases the SQLite suite pins, re-run on the real dialect: a
    stopped tree is suppressed even when its ROOT already finished, a live tree
    is delivered, and a NULL root is never suppressed.
    """
    ch = f"{_PREFIX}_ch1"
    await mysql_client.insert(
        "bus_channels",
        {"channel_id": ch, "name": "room", "channel_type": "group", "created_by": "team_1"},
    )
    for a in (f"{_PREFIX}_agent_a", f"{_PREFIX}_agent_b"):
        await mysql_client.insert(
            "bus_channel_members", {"channel_id": ch, "agent_id": a}
        )
        await mysql_client.insert(
            "agents", {"agent_id": a, "agent_name": a, "created_by": "u"}
        )

    # Stopped tree whose root already finished (the delegating shape).
    await _seed_run(
        mysql_client, f"{_PREFIX}_root", root=f"{_PREFIX}_root", state="completed"
    )
    await _seed_run(mysql_client, f"{_PREFIX}_child", root=f"{_PREFIX}_root", stopped=True)
    # A live tree.
    await _seed_run(mysql_client, f"{_PREFIX}_live", root=f"{_PREFIX}_live")

    bus = LocalMessageBus(backend=mysql_client._backend)
    await bus.send_message(
        from_agent=f"{_PREFIX}_agent_a", to_channel=ch, content="stopped tree",
        root_run_id=f"{_PREFIX}_root",
    )
    await bus.send_message(
        from_agent=f"{_PREFIX}_agent_a", to_channel=ch, content="live tree",
        root_run_id=f"{_PREFIX}_live",
    )
    await bus.send_message(
        from_agent=f"{_PREFIX}_agent_a", to_channel=ch, content="no tree",
    )

    pending = await bus.get_pending_messages(f"{_PREFIX}_agent_b")

    contents = {m.content for m in pending}
    assert "stopped tree" not in contents
    assert "live tree" in contents
    assert "no tree" in contents
