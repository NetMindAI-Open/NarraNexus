"""
@file_name: test_team_posting_mysql.py
@author:
@date: 2026-08-18
@description: Real-MySQL coverage for the team-posting lane's hand-written SQL.

Two new raw statements arrive with the harness redesign, both on paths that run
on ordinary traffic rather than on an error branch:

  * `team_posting.team_cascade_depth` — the hop cap. Its `NOT IN (…)` list is
    built from a variable number of placeholders, and it is the guard that stops
    an @mention cascade from looping. When it raised, it did not degrade to "no
    cap": it took the whole send down, and every `message_team` returned
    `{"success": false}`.
  * `_message_bus_mcp_tools._resolve_conversation` — the DM lookup behind
    `read_history`, a three-way join whose two joined member rows ARE the
    authorisation.

The SQLite suite structurally cannot see a dialect error in either: these run
through `AsyncDatabaseClient`, which rewrites `%s` per backend, so what SQLite
executes and what MySQL executes are different text. `bus_wake` is exercised
too — one row updated on every send is the shape where the two engines' upsert
and locking semantics differ most, and `wake_signal.bump` fails open, so a
dialect error there would be invisible except as messages arriving late.

Project policy for new raw SQL, same as `test_unread_cursor_mysql.py` and
`test_cascade_stop_mysql.py`.

Enable with a throwaway MySQL DSN:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.schema_registry import auto_migrate

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"
_PREFIX = "mysqlteampost"
LEAD = f"{_PREFIX}_lead"
PEER = f"{_PREFIX}_peer"
ROOM = f"{_PREFIX}_room"
DM = f"{_PREFIX}_dm"
TEAM = f"{_PREFIX}_t1"


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


pytestmark = pytest.mark.skipif(
    not os.environ.get(MYSQL_URL_ENV),
    reason=(
        f"{MYSQL_URL_ENV} not set. Validates the hop cap's variable-placeholder "
        f"NOT IN, the DM lookup's three-way join and the wake signal's upsert "
        f"against a real MySQL dialect. "
        f"Example DSN: mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
    ),
)

_CHANNELS = (ROOM, DM)


@pytest_asyncio.fixture
async def env():
    backend = MySQLBackend(_parse_mysql_url(os.environ[MYSQL_URL_ENV]))
    await backend.initialize()
    await auto_migrate(backend)
    db = AsyncDatabaseClient(_backend=backend)
    bus = LocalMessageBus(backend=backend)

    async def _clean():
        for cid in _CHANNELS:
            for table in ("bus_messages", "bus_channel_members", "bus_channels"):
                await backend.execute_write(
                    f"DELETE FROM {table} WHERE channel_id = %s", (cid,)
                )
        await backend.execute_write(
            "DELETE FROM team_members WHERE team_id = %s", (TEAM,)
        )

    await _clean()
    await backend.execute_write(
        "INSERT INTO bus_channels (channel_id, name, channel_type, created_by) "
        "VALUES (%s, %s, %s, %s)",
        (ROOM, "room", "group", f"team_{TEAM}"),
    )
    await backend.execute_write(
        "INSERT INTO bus_channels (channel_id, name, channel_type, created_by) "
        "VALUES (%s, %s, %s, %s)",
        (DM, "dm", "direct", LEAD),
    )
    for cid in _CHANNELS:
        for aid in (LEAD, PEER):
            await backend.execute_write(
                "INSERT INTO bus_channel_members (channel_id, agent_id) "
                "VALUES (%s, %s)",
                (cid, aid),
            )
    await backend.execute_write(
        "INSERT INTO team_members (team_id, agent_id) VALUES (%s, %s)", (TEAM, LEAD)
    )
    yield db, bus, backend
    try:
        await _clean()
    except Exception:  # noqa: BLE001 — teardown must not mask a failure
        pass
    await backend.close()


@pytest.mark.asyncio
async def test_the_hop_cap_query_runs_on_mysql_and_counts_agent_hops(env):
    """The variable-placeholder `NOT IN` plus the user-message stop condition."""
    from narranexus.platform.message_bus.team_posting import team_cascade_depth

    db, bus, _ = env

    assert await team_cascade_depth(db, ROOM) == 0

    await bus.send_message(from_agent="usr_1", to_channel=ROOM, content="kick off")
    await bus.send_message(from_agent=LEAD, to_channel=ROOM, content="on it")
    await bus.send_message(from_agent=PEER, to_channel=ROOM, content="and me")
    # Two agent messages since the user spoke.
    assert await team_cascade_depth(db, ROOM) == 2

    # A user message resets the chain — that is the stop condition, and it is
    # read from rows the NOT IN did not filter out.
    await bus.send_message(from_agent="usr_1", to_channel=ROOM, content="thanks")
    assert await team_cascade_depth(db, ROOM) == 0


@pytest.mark.asyncio
async def test_platform_rows_are_excluded_in_sql_not_afterwards(env):
    """The invariant the cap actually rests on, asserted on the LIVE query.

    The statement carries `LIMIT MAX_TEAM_AGENT_HOPS + 2`, so platform notices
    inside the window would push real agent hops out of it and the cap would
    silently stop firing in exactly the rooms that need it most — the ones busy
    enough for patrol to be talking. Filtering after the fetch cannot fix that;
    the exclusion has to be in the WHERE, and this asserts it through MySQL's
    own `NOT IN` rather than through Python.
    """
    from narranexus.platform.message_bus.team_posting import team_cascade_depth
    from narranexus.platform.message_bus.system_messages import PLATFORM_MSG_TYPES

    db, bus, _ = env

    await bus.send_message(from_agent="usr_1", to_channel=ROOM, content="go")
    await bus.send_message(from_agent=LEAD, to_channel=ROOM, content="hop 1")
    for i, mt in enumerate(sorted(PLATFORM_MSG_TYPES)):
        await bus.send_message(
            from_agent=f"{_PREFIX}_platform", to_channel=ROOM,
            content=f"notice {i}", msg_type=mt,
        )
    await bus.send_message(from_agent=PEER, to_channel=ROOM, content="hop 2")

    assert await team_cascade_depth(db, ROOM) == 2, (
        "platform notices were counted as agent hops, or pushed a real hop out "
        "of the LIMIT window"
    )


@pytest.mark.asyncio
async def test_the_dm_lookup_join_runs_on_mysql_and_gates_on_membership(env):
    from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
        _resolve_conversation,
    )

    db, _bus, _ = env

    import narranexus.platform.utils.db.db_factory as db_factory

    async def _async_db():
        return db

    original = db_factory.get_db_client
    db_factory.get_db_client = _async_db
    try:
        channel_id, err = await _resolve_conversation(
            LEAD, with_agent=PEER, team_id=""
        )
        assert err is None and channel_id == DM

        # A non-member gets nothing back — the join, not a follow-up check.
        channel_id, err = await _resolve_conversation(
            f"{_PREFIX}_outsider", with_agent=PEER, team_id=""
        )
        assert channel_id is None and err

        # And the team branch, whose gate is a `team_members` row.
        channel_id, err = await _resolve_conversation(LEAD, with_agent="", team_id=TEAM)
        assert err is None and channel_id == ROOM
    finally:
        db_factory.get_db_client = original


@pytest.mark.asyncio
async def test_the_wake_signal_bumps_and_reads_on_mysql(env):
    """One row, updated on every send, from every process.

    `bump` is update-then-insert rather than a dialect-specific upsert, and it fails
    open — so on MySQL a broken statement here would show up only as messages
    arriving a poll interval late, with nothing in the logs above debug. That is the
    failure this twin exists to make loud.

    **Which is why the assertions must be falsifiable, and the first version was
    not.** It asserted `first is not None` and `second >= first`. `wake_signal.read`
    returns `""` on any exception AND `""` when the row is missing, so both held
    with a completely broken backend — replayed against a backend whose every
    method raised: all four assertions passed. A twin that certifies a dead lane is
    worse than no twin, because the greenness is read as coverage.

    `!=` is the assertion that cannot be satisfied by `""`; the second bump is
    strictly greater, because `>=` is satisfied by "the bump did nothing" whenever
    two reads land in the same microsecond. Same shape as the SQLite twin
    (`test_cross_process_wake.py`), which was correct from the start.
    """
    from narranexus.platform.message_bus import wake_signal

    db, _bus, _ = env

    before = await wake_signal.read(db)
    await wake_signal.bump(db)
    first = await wake_signal.read(db)
    assert first != before, (
        f"the signal did not change across a bump ({before!r} -> {first!r}) — on "
        f"MySQL a failing bump is swallowed at debug, so this is the only place it "
        f"becomes visible"
    )
    assert first, "the signal read back empty, i.e. unreadable or absent"

    await wake_signal.bump(db)
    second = await wake_signal.read(db)
    assert second > first, (
        f"the second bump did not advance the signal ({first!r} -> {second!r}); "
        f">= would have accepted a bump that did nothing"
    )
