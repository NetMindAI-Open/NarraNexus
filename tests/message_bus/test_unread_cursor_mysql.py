"""
@file_name: test_unread_cursor_mysql.py
@author:
@date: 2026-08-11
@description: Real-MySQL coverage for the unread cursor's hand-written SQL.

Three new raw statements land in `local_bus.py` with this change: `ack_read`'s
guarded UPDATE, `count_unread`'s COUNT, and `get_unread`'s two-parameter WHERE.
The SQLite suite structurally cannot see a dialect error in any of them —
`LocalMessageBus._db` is the RAW backend, so what SQLite runs and what MySQL
runs are not the same text.

Project policy for new raw SQL, same as `test_cascade_stop_mysql.py` and
`test_team_work_repository_mysql.py`.

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
from narranexus.platform.utils.db.schema_registry import auto_migrate

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"
_PREFIX = "mysqlunread"
ME = f"{_PREFIX}_me"
PEER = f"{_PREFIX}_peer"
ROOM = f"{_PREFIX}_room"


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
        f"{MYSQL_URL_ENV} not set. Validates the unread cursor's raw SQL against "
        f"a real MySQL dialect (guarded UPDATE + COUNT + two-parameter WHERE). "
        f"Example DSN: mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
    ),
)


@pytest_asyncio.fixture
async def bus():
    backend = MySQLBackend(_parse_mysql_url(os.environ[MYSQL_URL_ENV]))
    await backend.initialize()
    await auto_migrate(backend)
    b = LocalMessageBus(backend=backend)
    await backend.execute_write(
        "DELETE FROM bus_messages WHERE channel_id = %s", (ROOM,)
    )
    await backend.execute_write(
        "DELETE FROM bus_channel_members WHERE channel_id = %s", (ROOM,)
    )
    await backend.execute_write(
        "DELETE FROM bus_channels WHERE channel_id = %s", (ROOM,)
    )
    await backend.execute_write(
        "INSERT INTO bus_channels (channel_id, name, channel_type, created_by) "
        "VALUES (%s, %s, %s, %s)",
        (ROOM, "room", "group", "team_t1"),
    )
    for aid in (ME, PEER):
        await backend.execute_write(
            "INSERT INTO bus_channel_members (channel_id, agent_id) VALUES (%s, %s)",
            (ROOM, aid),
        )
    yield b
    try:
        for table, col in (
            ("bus_messages", "channel_id"),
            ("bus_channel_members", "channel_id"),
            ("bus_channels", "channel_id"),
        ):
            await backend.execute_write(
                f"DELETE FROM {table} WHERE {col} = %s", (ROOM,)
            )
    except Exception:  # noqa: BLE001 — teardown must not mask a failure
        pass
    await backend.close()


@pytest.mark.asyncio
async def test_get_unread_and_count_run_on_mysql(bus):
    """The two-parameter WHERE (`cm.agent_id` AND `m.from_agent`) plus COUNT."""
    for i in range(4):
        await bus.send_message(from_agent=PEER, to_channel=ROOM, content=f"m{i}")
    await bus.send_message(from_agent=ME, to_channel=ROOM, content="mine")

    assert await bus.count_unread(ME) == 4
    assert [m.content for m in await bus.get_unread(ME, limit=2)] == ["m2", "m3"]
    assert len(await bus.get_unread(ME)) == 4


@pytest.mark.asyncio
async def test_ack_read_runs_on_mysql_and_only_moves_forward(bus):
    """The guarded UPDATE — its WHERE carries the same value twice."""
    await bus.send_message(from_agent=PEER, to_channel=ROOM, content="first")
    rows = await bus._db.execute(
        "SELECT created_at FROM bus_messages WHERE channel_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (ROOM,),
    )
    cutoff = rows[0]["created_at"]

    await bus.ack_read(ME, ROOM, cutoff)
    assert await bus.count_unread(ME) == 0

    # A stale timestamp must not drag the cursor back and resurface what was
    # already read.
    await bus.ack_read(ME, ROOM, "1971-01-01T00:00:00+00:00")
    assert await bus.count_unread(ME) == 0


@pytest.mark.asyncio
async def test_get_pending_channel_scope_runs_on_mysql(bus):
    """The new `AND m.channel_id = %s` branch of get_pending_messages, on a real
    MySQL dialect — the per-lane scoping that keeps a busy agent's other room
    from starving. SQLite alone cannot see a dialect error in the added WHERE."""
    other = f"{_PREFIX}_room2"
    await bus._db.execute_write(
        "DELETE FROM bus_messages WHERE channel_id = %s", (other,)
    )
    await bus._db.execute_write(
        "DELETE FROM bus_channel_members WHERE channel_id = %s", (other,)
    )
    await bus._db.execute_write(
        "DELETE FROM bus_channels WHERE channel_id = %s", (other,)
    )
    await bus._db.execute_write(
        "INSERT INTO bus_channels (channel_id, name, channel_type, created_by) "
        "VALUES (%s, %s, %s, %s)",
        (other, "room2", "group", "team_t1"),
    )
    await bus._db.execute_write(
        "INSERT INTO bus_channel_members (channel_id, agent_id) VALUES (%s, %s)",
        (other, ME),
    )
    try:
        await bus.send_message(from_agent=PEER, to_channel=ROOM, content="in-room")
        await bus.send_message(from_agent=PEER, to_channel=other, content="in-other")

        scoped = await bus.get_pending_messages(ME, channel_id=other)
        assert [m.content for m in scoped] == ["in-other"]
        # None keeps the cross-channel query.
        both = {m.channel_id for m in await bus.get_pending_messages(ME)}
        assert {ROOM, other} <= both
    finally:
        for table in ("bus_messages", "bus_channel_members", "bus_channels"):
            await bus._db.execute_write(
                f"DELETE FROM {table} WHERE channel_id = %s", (other,)
            )


@pytest.mark.asyncio
async def test_ack_processed_runs_on_mysql_and_only_moves_forward(bus):
    """The PROCESSED cursor's guarded UPDATE — the twin of ack_read's, added for
    live steering. Same shape (WHERE carries the value twice); a dialect error or
    a lost guard would let a running turn's older end-ack drag the cursor back
    behind steered messages and re-dispatch them as a fresh turn."""
    await bus.send_message(from_agent=PEER, to_channel=ROOM, content="older")
    await bus.send_message(from_agent=PEER, to_channel=ROOM, content="newer")
    rows = await bus._db.execute(
        "SELECT created_at FROM bus_messages WHERE channel_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (ROOM,),
    )
    newest = rows[0]["created_at"]

    await bus.ack_processed(ME, ROOM, newest)
    pend = [m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM]
    assert pend == []

    # A stale timestamp must be a no-op, not a rewind that resurfaces the batch.
    await bus.ack_processed(ME, ROOM, "1971-01-01T00:00:00+00:00")
    pend = [m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM]
    assert pend == []


@pytest.mark.asyncio
async def test_has_message_from_turn_runs_on_mysql(bus):
    """The three-column existence WHERE added for the team-room notice gate.

    A dialect error here would surface as an EXCEPTION inside the arm that
    decides whether to announce a delivery failure — i.e. exactly when the run
    is already degraded — so SQLite-only coverage is not enough.
    """
    await bus.send_message(
        from_agent=ME, to_channel=ROOM, content="mine", event_id="evt_x"
    )
    await bus.send_message(
        from_agent=PEER, to_channel=ROOM, content="theirs", event_id="evt_x"
    )

    assert await bus.has_message_from_turn(ROOM, ME, "evt_x") is True
    # Wrong turn, wrong sender, wrong channel: each column has to bite.
    assert await bus.has_message_from_turn(ROOM, ME, "evt_other") is False
    assert await bus.has_message_from_turn(ROOM, f"{_PREFIX}_ghost", "evt_x") is False
    assert await bus.has_message_from_turn("ch_nope", ME, "evt_x") is False


@pytest.mark.asyncio
async def test_has_unread_before_runs_on_mysql_and_respects_the_legacy_filter(bus):
    """The third reader of `_unread_predicate`, and the only one doing timestamp work.

    `has_unread_before` adds `AND m.created_at < %s` with `canonical_ts(before)`.
    On SQLite `created_at` is TEXT and that is a lexicographic string compare; on
    MySQL it is `DATETIME(6)` and the engine casts the string. `canonical_ts` is
    `value.isoformat()` over a value whose type came from whichever backend parsed
    the row, so the two engines are fed different literal SHAPES into the same
    comparison — the case a twin exists for.

    If the cast degrades on MySQL the method returns False, its caller skips its
    early return, and `ack_read` advances the cursor to the trigger message —
    permanently marking as read every unread message older than the rendered
    window. Irreversible context loss on the cloud deploy, and the call is wrapped
    in `except Exception`, so even a hard failure degrades to a warning line. This
    lane has already been bitten by a timestamp comparison once (`canonical_ts`'s
    own docstring: "That cost us a re-trigger loop once").

    `before` is taken from a `BusMessage.created_at` READ BACK FROM MySQL, not
    hand-written: a literal built in Python proves nothing about the shape
    mismatch, which is the whole point.

    Also asserts the interleaved parameter tuple parses on a real engine — this is
    the one caller whose prefix params sit between the predicate's own and its
    extras.
    """
    await bus.send_message(from_agent=PEER, to_channel=ROOM, content="older")
    await bus.send_message(from_agent=PEER, to_channel=ROOM, content="newer")

    msgs = await bus.get_unread(ME)
    assert [m.content for m in msgs] == ["older", "newer"]
    older, newer = msgs[0], msgs[1]

    # Something unread strictly older than the NEWER message: the older one.
    assert await bus.has_unread_before(ME, ROOM, newer.created_at) is True
    # Nothing is older than the OLDEST (the comparison is exclusive).
    assert await bus.has_unread_before(ME, ROOM, older.created_at) is False

    # A legacy IM channel answers False even asked about directly — the prefix
    # exclusion has to apply in this reader too, or the cursor logic sees rows the
    # context never rendered.
    from narranexus.platform.channel.message_source_handler import im_channel_prefixes

    legacy = f"{im_channel_prefixes()[0]}{_PREFIX}_legacy"
    await bus._db.execute_write(
        "INSERT INTO bus_channels (channel_id, name, channel_type, created_by) "
        "VALUES (%s, %s, %s, %s)",
        (legacy, "legacy", "direct", ME),
    )
    await bus._db.execute_write(
        "INSERT INTO bus_channel_members (channel_id, agent_id) VALUES (%s, %s)",
        (legacy, ME),
    )
    try:
        await bus.send_message(
            from_agent="lark_user_1", to_channel=legacy, content="legacy row"
        )
        assert await bus.has_unread_before(ME, legacy, newer.created_at) is False
        assert await bus.count_unread(ME) == 2, "the legacy row was counted"
    finally:
        for table in ("bus_messages", "bus_channel_members", "bus_channels"):
            await bus._db.execute_write(
                f"DELETE FROM {table} WHERE channel_id = %s", (legacy,)
            )
