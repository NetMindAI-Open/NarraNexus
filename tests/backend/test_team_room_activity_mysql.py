"""
@file_name: test_team_room_activity_mysql.py
@author: NarraNexus
@date: 2026-08-14
@description: The room-activity query against a real MySQL dialect.

`_team_room_activity` is the one piece of SQL in this change whose dialect
differences would show up in RESULTS rather than in an error: `created_at` is
TEXT compared lexicographically on SQLite and `DATETIME(6)` compared as a time
on MySQL, and the query leans on that comparison twice — once inside a `MAX()`
per channel, once joining that maximum back to the row that produced it.

Everything else about it (a derived table with an alias, `GROUP BY`, `NOT IN`,
`IS NULL`) is common to both, and `ONLY_FULL_GROUP_BY` is satisfied. But
"probably fine" is exactly the confidence level that produced the last two
dialect incidents, and a wrong answer here is silent: the sidebar shows the
wrong message, or no mark at all, and nothing errors.

Run with a throwaway MySQL:

    docker run --rm -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root \\
        -e MYSQL_DATABASE=nxtest --name nx-mysql-test mysql:8
    export NARRANEXUS_MYSQL_TEST_URL=mysql://root:root@127.0.0.1:3306/nxtest
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from narranexus.platform.message_bus.system_messages import PLATFORM_MSG_TYPES
from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

from narranexus_plugins.teams.routes import _team_room_activity

from tests.mysql_dialect import (
    mysql_configured,
    mysql_url,
    parse_mysql_url,
    skip_reason,
)

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "the room-activity MAX + self-join, whose `created_at` is a DATETIME(6) "
        "here and a lexicographically-compared TEXT on SQLite"
    ),
)

_PREFIX = "mysqlact"
TEAM = f"{_PREFIX}_team_1"
OTHER = f"{_PREFIX}_team_2"
USER = f"usr_{_PREFIX}"


# Channel ids are deterministic, so a run that died between the inserts and the
# teardown leaves rows that make the NEXT run collide on the primary key — and
# that failure reads like a dialect bug, which is the one thing this file exists
# to detect. Cleaned BEFORE as well as after, following the convention in
# `tests/message_bus/test_unread_cursor_mysql.py`.
_SCOPED = (
    ("bus_messages", "channel_id"),
    ("bus_channels", "channel_id"),
    ("teams", "team_id"),
)


async def _clean(client, *, quiet: bool) -> None:
    """Remove this file's rows.

    `quiet` only for TEARDOWN: a cleanup failure there must not mask the real
    result. Before the test it is the opposite — swallowing it lets leftovers
    collide on the primary key mid-test, and that error reads like the dialect
    bug this file exists to detect, which is precisely the confusion the
    pre-clean was added to prevent.
    """
    for table, col in _SCOPED:
        try:
            await client.execute(
                f"DELETE FROM {table} WHERE {col} LIKE %s", (f"{_PREFIX}%",), fetch=False
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


async def _room(db, team_id: str, suffix: str = "") -> str:
    channel_id = f"{_PREFIX}_ch_{team_id}{suffix}"
    await db.insert(
        "bus_channels",
        {
            "channel_id": channel_id,
            "channel_type": "group",
            "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{team_id}",
            "name": "Room",
        },
    )
    return channel_id


async def _say(db, channel_id: str, sender: str, content: str, at: str, msg_type="text"):
    await db.insert(
        "bus_messages",
        {
            "message_id": f"{_PREFIX}_{channel_id}_{at}",
            "channel_id": channel_id,
            "from_agent": sender,
            "content": content,
            "msg_type": msg_type,
            "created_at": at,
        },
    )


def _ts(i: int) -> str:
    return f"2026-08-14 12:{i // 60:02d}:{i % 60:02d}"


@pytest.mark.asyncio
async def test_the_newest_message_wins_under_a_datetime_column(mysql_client):
    """The `MAX()` and the join both compare a real DATETIME(6) here, not text."""
    channel = await _room(mysql_client, TEAM)
    await _say(mysql_client, channel, "agent_a", "older", _ts(1))
    await _say(mysql_client, channel, "agent_a", "newest", _ts(9))
    await _say(mysql_client, channel, "agent_a", "middle", _ts(5))

    got = await _team_room_activity(mysql_client, [TEAM], user_sender=USER)

    assert got[TEAM]["preview"] == "newest"


@pytest.mark.asyncio
async def test_the_exclusions_hold_in_this_dialect_too(mysql_client):
    """`from_agent != %s` and the `NOT IN` over platform types — the two rules
    that decide whether the mark is trustworthy at all."""
    channel = await _room(mysql_client, TEAM)
    await _say(mysql_client, channel, "agent_a", "the real answer", _ts(1))
    await _say(mysql_client, channel, USER, "thanks", _ts(2))
    await _say(mysql_client, channel, "agent_a", "swept", _ts(3), msg_type=PLATFORM_MSG_TYPES[0])

    got = await _team_room_activity(mysql_client, [TEAM], user_sender=USER)

    assert got[TEAM]["preview"] == "the real answer"


@pytest.mark.asyncio
async def test_rooms_stay_apart_when_several_are_queried_at_once(mysql_client):
    """The whole point of the single query: one statement, many rooms, and the
    `GROUP BY` has to keep them separate."""
    ours = await _room(mysql_client, TEAM)
    theirs = await _room(mysql_client, OTHER)
    await _say(mysql_client, ours, "agent_a", "ours", _ts(1))
    await _say(mysql_client, theirs, "agent_a", "theirs", _ts(2))

    got = await _team_room_activity(mysql_client, [TEAM, OTHER], user_sender=USER)

    assert got[TEAM]["preview"] == "ours"
    assert got[OTHER]["preview"] == "theirs"


@pytest.mark.asyncio
async def test_a_silent_room_reports_nothing(mysql_client):
    """An empty result set, not a row of NULLs — the caller distinguishes the
    two by presence, and a NULL row would render as a mark with no message."""
    await _room(mysql_client, TEAM)

    assert await _team_room_activity(mysql_client, [TEAM], user_sender=USER) == {}


@pytest.mark.asyncio
async def test_a_team_with_two_rooms_reports_the_newer_one(mysql_client):
    """The comparison the previous commit rewrote, in the dialect it is most
    likely to behave differently in.

    This twin covered "newest wins", "the exclusions hold" and "rooms stay
    apart" — everything except the branch that was actually being changed. The
    comparison reads the RAW column, whose wire type is exactly what differs
    between the two backends (`datetime` here, string there), so single-dialect
    coverage of it is coverage of the half that cannot break.

    Timestamps are given at INSERT rather than patched afterwards: the room's
    incremental polling rests on bus_messages being append-only, and a test that
    mutates a row to make its point sits awkwardly next to that.
    """
    old_room = await _room(mysql_client, TEAM, "_a")
    new_room = await _room(mysql_client, TEAM, "_b")
    await _say(mysql_client, old_room, "agent_a", "older", _ts(1))
    await _say(mysql_client, new_room, "agent_a", "newer", _ts(6))

    got = await _team_room_activity(mysql_client, [TEAM], user_sender=USER)

    assert got[TEAM]["preview"] == "newer"


# A NULL `created_at` is unreachable: the column is NOT NULL in both dialects, so
# the `str(None) == "None"` failure that motivated using `event_time_str` here
# cannot be produced by any test. The swap is still right — one helper instead of
# a fourth hand-rolled copy, and it normalises the datetime/string asymmetry this
# file exists for — but within ONE backend both operands always share a wire
# type, so nothing here can observe the difference. Saying so beats writing a
# test that asserts a state the schema forbids.
