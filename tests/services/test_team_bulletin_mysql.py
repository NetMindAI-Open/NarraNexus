"""
@file_name: test_team_bulletin_mysql.py
@author: NarraNexus
@date: 2026-08-11
@description: The bulletin's raw SQL against a real MySQL dialect.

Every statement here is hand-written SQL rather than a repository helper, which
is exactly where a sqlite/MySQL divergence hides: sqlite accepts a great deal
that MySQL rejects, and the sqlite suite would stay green while production —
which runs MySQL — fell over on the first request. That has happened in this
codebase before; `tests/message_bus/test_team_workspace_mysql.py` exists for the
same reason and this file follows its shape.

The statements at risk:

  * `source != %s` with a bound parameter in `usage()` and `delete_tier()`;
  * a bound `LIMIT %s` in the summariser's transcript read (MySQL is stricter
    about placeholders in LIMIT than sqlite);
  * a `created_at > %s` comparison against a DATETIME(6) column fed a string —
    the watermark is a string on the way in, and MySQL's coercion rules there
    are not sqlite's;
  * `COUNT(*) AS n` aliasing, which the worker reads back by name.

Enable with a throwaway MySQL:

    docker run -d --name nx-mysql -e MYSQL_ROOT_PASSWORD=root \\
        -e MYSQL_DATABASE=nxtest -p 3306:3306 mysql:8
    export NARRANEXUS_MYSQL_TEST_URL=mysql://root:root@127.0.0.1:3306/nxtest
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from narranexus.platform.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)
from narranexus_plugins.teams.summary_worker import TeamSummaryWorker
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"


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
        f"{MYSQL_URL_ENV} not set. These tests validate the team-bulletin raw "
        f"SQL against a real MySQL dialect (bound LIMIT, a DATETIME(6) "
        f"comparison fed a string watermark, and `source != %s` filters)."
    ),
)

_PREFIX = "mysqlbul"
TEAM = f"{_PREFIX}_team_1"
OTHER = f"{_PREFIX}_team_2"
CHANNEL = f"{_PREFIX}_ch_1"
OWNER = f"{_PREFIX}_user"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(_parse_mysql_url(os.environ[MYSQL_URL_ENV]))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    # Shared throwaway DB: clean only what this file created.
    for table, col in (
        ("team_bulletin_entries", "team_id"),
        ("bus_messages", "channel_id"),
        ("bus_channels", "channel_id"),
        ("team_members", "team_id"),
        ("teams", "team_id"),
    ):
        try:
            await client.execute(f"DELETE FROM {table} WHERE {col} LIKE %s", (f"{_PREFIX}%",), fetch=False)
        except Exception:  # noqa: BLE001 — teardown must not mask a failure
            pass
    await client.close()


def _ts(i: int) -> str:
    return f"2026-08-11 12:{i // 60:02d}:{i % 60:02d}"


# ── the table exists in the MySQL dialect at all ────────────────────────────


@pytest.mark.asyncio
async def test_the_table_is_provisioned_by_auto_migrate(mysql_client):
    """If the DDL generator produced something MySQL rejects, every other test
    here fails for the same uninformative reason — so check it first."""
    rows = await mysql_client.execute(
        "SELECT COUNT(*) AS n FROM team_bulletin_entries WHERE team_id = %s",
        (TEAM,),
        fetch=True,
    )
    assert int(rows[0]["n"]) == 0


# ── repository SQL ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usage_excludes_the_summary_on_mysql(mysql_client):
    """`source != %s` with a bound parameter."""
    repo = TeamBulletinRepository(mysql_client)
    await repo.add(team_id=TEAM, content="a rule", source="user", author_id=OWNER)
    await repo.upsert_summary(TEAM, "some progress")

    usage = await repo.usage(TEAM)
    assert usage.entry_count == 1
    assert usage.total_chars == len("a rule")


@pytest.mark.asyncio
async def test_list_is_ordered_and_team_scoped_on_mysql(mysql_client):
    repo = TeamBulletinRepository(mysql_client)
    for c in ("first", "second", "third"):
        await repo.add(team_id=TEAM, content=c, source="user", author_id=OWNER)
    await repo.add(team_id=OTHER, content="theirs", source="user", author_id=OWNER)

    assert [e.content for e in await repo.list_for_team(TEAM)] == [
        "first",
        "second",
        "third",
    ]


@pytest.mark.asyncio
async def test_delete_tier_spares_the_summary_on_mysql(mysql_client):
    """Two bound params plus a `!=` in one DELETE."""
    repo = TeamBulletinRepository(mysql_client)
    await repo.add(team_id=TEAM, content="standing", source="user", author_id=OWNER)
    await repo.add(
        team_id=TEAM,
        content="this task",
        source="user",
        author_id=OWNER,
        tier="current_task",
    )
    await repo.upsert_summary(TEAM, "progress")

    removed = await repo.delete_tier(TEAM, "current_task")

    assert removed == 1
    assert await repo.get_summary(TEAM) is not None
    assert [e.content for e in await repo.list_for_team(TEAM) if e.source == "user"] == ["standing"]


@pytest.mark.asyncio
async def test_the_summary_slot_upserts_rather_than_accumulating_on_mysql(mysql_client):
    repo = TeamBulletinRepository(mysql_client)
    await repo.upsert_summary(TEAM, "v1")
    await repo.upsert_summary(TEAM, "v2")

    summaries = [e for e in await repo.list_for_team(TEAM) if e.source == "auto_summary"]
    assert len(summaries) == 1
    assert summaries[0].content == "v2"


# ── worker SQL ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_worker_finds_team_rooms_on_mysql(mysql_client):
    await mysql_client.insert("teams", {"team_id": TEAM, "owner_user_id": OWNER, "name": "T"})
    await mysql_client.insert(
        "bus_channels",
        {
            "channel_id": CHANNEL,
            "channel_type": "group",
            "created_by": f"team_{TEAM}",
            "name": "T",
        },
    )

    rooms = await TeamSummaryWorker(mysql_client)._team_rooms()

    assert {r["team_id"] for r in rooms if r["channel_id"] == CHANNEL} == {TEAM}


@pytest.mark.asyncio
async def test_the_watermark_comparison_works_against_datetime6(mysql_client):
    """`created_at > %s` fed a STRING against a DATETIME(6) column. MySQL's
    coercion rules here are not sqlite's, and this is the trigger's whole
    arithmetic — silently matching nothing would mean no team is ever
    summarised in production while every sqlite test stays green."""
    await mysql_client.insert(
        "bus_channels",
        {
            "channel_id": CHANNEL,
            "channel_type": "group",
            "created_by": f"team_{TEAM}",
            "name": "T",
        },
    )
    for i in range(6):
        await mysql_client.insert(
            "bus_messages",
            {
                "message_id": f"{_PREFIX}_m{i}",
                "channel_id": CHANNEL,
                "from_agent": "a",
                "content": f"step {i}",
                "msg_type": "text",
                "created_at": _ts(i),
            },
        )

    w = TeamSummaryWorker(mysql_client)
    assert await w._new_message_count(CHANNEL, None) == 6
    assert await w._new_message_count(CHANNEL, _ts(2)) == 3
    assert await w._new_message_count(CHANNEL, _ts(5)) == 0


@pytest.mark.asyncio
async def test_the_transcript_read_uses_a_bound_limit_on_mysql(mysql_client):
    """A placeholder in LIMIT — MySQL is stricter about this than sqlite."""
    await mysql_client.insert(
        "bus_channels",
        {
            "channel_id": CHANNEL,
            "channel_type": "group",
            "created_by": f"team_{TEAM}",
            "name": "T",
        },
    )
    for i in range(4):
        await mysql_client.insert(
            "bus_messages",
            {
                "message_id": f"{_PREFIX}_m{i}",
                "channel_id": CHANNEL,
                "from_agent": "a",
                "content": f"step {i}",
                "msg_type": "text",
                "created_at": _ts(i),
            },
        )

    transcript, newest = await TeamSummaryWorker(mysql_client)._transcript(CHANNEL)

    assert "step 0" in transcript and "step 3" in transcript
    assert newest is not None


@pytest.mark.asyncio
async def test_the_watermark_round_trips_on_mysql(mysql_client):
    """Written as a string into DATETIME(6), read back and compared again."""
    repo = TeamBulletinRepository(mysql_client)
    await repo.upsert_summary(TEAM, "progress")
    w = TeamSummaryWorker(mysql_client)

    await w._set_watermark(TEAM, _ts(10))
    got = await w._watermark(TEAM)

    assert got is not None
    assert str(got).startswith("2026-08-11")


@pytest.mark.asyncio
async def test_an_end_to_end_pass_writes_a_summary_on_mysql(mysql_client):
    """The whole trigger→transcript→upsert→watermark chain on the real dialect."""
    await mysql_client.insert("teams", {"team_id": TEAM, "owner_user_id": OWNER, "name": "T"})
    # KNOWN DUPLICATION (2026-08-17, deliberate): "what a summarisable team needs
    # seeded" now lives here AND in `test_team_summary_worker.py::_seed_room`.
    # Those two drifting apart is what broke this test in the first place. Not
    # extracted into a shared helper in the same change, for one reason worth
    # writing down: the MySQL twin can only be executed in CI (no container on
    # the authoring machine), and a seeding refactor whose sole verification is a
    # CI round trip is not a good trade against a duplication that is currently
    # identical. What changed is the cost of the next drift: with pytest in CI
    # and the dialect service up, the same mistake now fails loudly on the next
    # PR instead of skipping silently for weeks. Whoever adds the NEXT gate to
    # `_summarise_team` should extract `_seed_room` (parameterise TEAM / CHANNEL /
    # message-id prefixes — this file's cleanup deletes by `_PREFIX` LIKE, so the
    # prefixes must stay injectable).
    #
    # A member is REQUIRED for the chain to run at all: `_summarise_team` gates on
    # `_cost_bearer(team_id)` and skips a memberless team, because no member means
    # no cost bearer and the tokens would go unrecorded. That gate landed after
    # this twin was written, and the twin skipped on every machine without a
    # MySQL URL and was never run in CI — so it rotted into asserting the tail of
    # a chain that had already returned early. First CI run with the dialect
    # service caught it (2026-08-17). The SQLite sibling
    # (`test_team_summary_worker.py::_seed_room`) has always seeded this.
    await mysql_client.insert("team_members", {"team_id": TEAM, "agent_id": "agent_a"})
    await mysql_client.insert(
        "bus_channels",
        {
            "channel_id": CHANNEL,
            "channel_type": "group",
            "created_by": f"team_{TEAM}",
            "name": "T",
        },
    )
    for i in range(TeamSummaryWorker.MESSAGE_THRESHOLD):
        await mysql_client.insert(
            "bus_messages",
            {
                "message_id": f"{_PREFIX}_m{i}",
                "channel_id": CHANNEL,
                "from_agent": "a",
                "content": f"step {i}",
                "msg_type": "text",
                "created_at": _ts(i),
            },
        )

    w = TeamSummaryWorker(mysql_client)

    async def fake(*, team_id, transcript, bearer=""):
        return "the team is halfway"

    w._summarise = fake
    await w.run_once()

    summary = await TeamBulletinRepository(mysql_client).get_summary(TEAM)
    assert summary is not None
    assert summary.content == "the team is halfway"
    # And a second pass must not re-summarise: the watermark landed.
    calls = []

    async def counting(*, team_id, transcript, bearer=""):
        calls.append(team_id)
        return "again"

    w._summarise = counting
    await w.run_once()
    assert calls == []
