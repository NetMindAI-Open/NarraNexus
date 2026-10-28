"""
@file_name: test_team_work_repository_mysql.py
@author:
@date: 2026-08-10
@description: Real-MySQL coverage for the work board's hand-written SQL.

`TeamWorkItemRepository` carries five raw statements, and the SQLite suite
structurally cannot see a dialect error in them: `AsyncDatabaseClient.execute`
translates `%s` only on the sqlite branch, so SQLite runs the rewritten text
while MySQL runs the original. `test_trigger_reserved_word_sql.py` records
where that ends — green locally, 1064 in prod, swallowed by a bare except for
two days.

Project policy for new raw SQL, same as `test_agents_bus_failures_mysql.py`
and (this feature's predecessor) `test_cascade_stop_mysql.py`.

Enable with a throwaway MySQL DSN:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_work_schema import WorkItemOrigin, WorkItemStatus
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"
_PREFIX = "mysqlwb"


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
        f"{MYSQL_URL_ENV} not set. Validates the work board's raw SQL against a "
        f"real MySQL dialect (generated IN-lists + a parameterised WHERE). "
        f"Example DSN: mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
    ),
)


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(_parse_mysql_url(os.environ[MYSQL_URL_ENV]))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    try:
        await client.execute(
            "DELETE FROM team_work_items WHERE team_id LIKE %s", (f"{_PREFIX}%",),
            fetch=False,
        )
    except Exception:  # noqa: BLE001 — teardown must not mask a failure
        pass
    await client.close()


@pytest.mark.asyncio
async def test_list_active_runs_on_mysql(mysql_client):
    """`WHERE team_id = %s AND status IN (%s,%s,%s)` — a generated placeholder
    list, the shape that broke before."""
    repo = TeamWorkItemRepository(mysql_client)
    team = f"{_PREFIX}_t1"
    live = await repo.create_item(team_id=team, channel_id="ch", title="live",
                                  created_by="agent_lead")
    parked = await repo.create_item(team_id=team, channel_id="ch", title="parked",
                                    created_by="agent_lead", root_run_id=f"{_PREFIX}_r")
    await repo.pause_by_root(f"{_PREFIX}_r")
    done = await repo.create_item(team_id=team, channel_id="ch", title="done",
                                  created_by="agent_lead")
    await repo.set_status(done.item_id, WorkItemStatus.DONE)
    del parked

    active = await repo.list_active(team)

    assert [i.item_id for i in active] == [live.item_id]


@pytest.mark.asyncio
async def test_list_visible_runs_on_mysql(mysql_client):
    """The board endpoint's query — same generated IN-list, one state wider.

    Polled every 5s by the panel, so a 1064 here would be a permanently broken
    board rather than an occasional error.
    """
    repo = TeamWorkItemRepository(mysql_client)
    team = f"{_PREFIX}_t3"
    live = await repo.create_item(team_id=team, channel_id="ch", title="live",
                                  created_by="agent_lead")
    parked = await repo.create_item(team_id=team, channel_id="ch", title="parked",
                                    created_by="agent_lead", root_run_id=f"{_PREFIX}_r3")
    await repo.pause_by_root(f"{_PREFIX}_r3")
    done = await repo.create_item(team_id=team, channel_id="ch", title="done",
                                  created_by="agent_lead")
    await repo.set_status(done.item_id, WorkItemStatus.DONE)

    visible = await repo.list_visible(team)

    assert [i.item_id for i in visible] == [live.item_id, parked.item_id]


@pytest.mark.asyncio
async def test_list_open_errands_runs_on_mysql(mysql_client):
    """The errand layer's close-side read — the fifth raw statement.

    Two extra parameters ahead of the generated IN-list (`channel_id`,
    `origin`), so it is a new placeholder shape, not a copy of `list_active`.

    Worth real-MySQL coverage more than the others: it runs on the delivery
    path of EVERY team-room message, and `errand.close_delivered_errands`
    swallows its failure into a warning. A 1064 here would not surface as an
    error anywhere — it would present as the board opening errands and never
    closing them, which is the one outcome `errand.py` documents as strictly
    worse than not having the feature.
    """
    repo = TeamWorkItemRepository(mysql_client)
    team = f"{_PREFIX}_t4"
    mine = await repo.create_item(
        team_id=team, channel_id="ch_here", title="mine", created_by="agent_lead",
        assignee_id="agent_b", origin=WorkItemOrigin.AUTO,
    )
    # Same agent, same team, DIFFERENT room: speaking here must not settle
    # what it owes there.
    await repo.create_item(
        team_id=team, channel_id="ch_elsewhere", title="other room",
        created_by="agent_lead", assignee_id="agent_b",
        origin=WorkItemOrigin.AUTO,
    )
    # Same room and agent, but a TASK — the Leader's to close, never ours.
    await repo.create_item(
        team_id=team, channel_id="ch_here", title="a task", created_by="agent_lead",
        assignee_id="agent_b", origin=WorkItemOrigin.TOOL,
    )
    # Already delivered: terminal states are outside ACTIVE.
    settled = await repo.create_item(
        team_id=team, channel_id="ch_here", title="settled",
        created_by="agent_lead", assignee_id="agent_b",
        origin=WorkItemOrigin.AUTO,
    )
    await repo.set_status(settled.item_id, WorkItemStatus.DONE)

    open_errands = await repo.list_open_errands("ch_here", "agent_b")

    assert [i.item_id for i in open_errands] == [mine.item_id]


@pytest.mark.asyncio
async def test_open_errands_come_back_oldest_first_on_mysql(mysql_client):
    """`close_delivered_errands` settles `open_errands[:1]`, so the ORDER BY is
    load-bearing, not cosmetic — it decides WHICH errand a delivery closes.

    Both rows are inserted in the same second on purpose: `created_at` alone
    cannot separate them, which is exactly why the statement's tiebreak is
    `id` (insertion order). Dropping that half would make the choice depend on
    whatever order the engine felt like returning.
    """
    repo = TeamWorkItemRepository(mysql_client)
    team = f"{_PREFIX}_t5"
    first = await repo.create_item(
        team_id=team, channel_id="ch_o", title="first", created_by="agent_lead",
        assignee_id="agent_c", origin=WorkItemOrigin.AUTO,
    )
    second = await repo.create_item(
        team_id=team, channel_id="ch_o", title="second", created_by="agent_lead",
        assignee_id="agent_c", origin=WorkItemOrigin.AUTO,
    )

    open_errands = await repo.list_open_errands("ch_o", "agent_c")

    assert [i.item_id for i in open_errands] == [first.item_id, second.item_id]


@pytest.mark.asyncio
async def test_teams_with_active_work_runs_on_mysql(mysql_client):
    repo = TeamWorkItemRepository(mysql_client)
    await repo.create_item(team_id=f"{_PREFIX}_live", channel_id="ch", title="x",
                           created_by="agent_lead")
    quiet = await repo.create_item(team_id=f"{_PREFIX}_quiet", channel_id="ch",
                                   title="y", created_by="agent_lead")
    await repo.set_status(quiet.item_id, WorkItemStatus.DONE)

    teams = await repo.teams_with_active_work()

    assert f"{_PREFIX}_live" in teams
    assert f"{_PREFIX}_quiet" not in teams


@pytest.mark.asyncio
async def test_pause_by_root_runs_on_mysql(mysql_client):
    """`_by_root`'s `WHERE root_run_id = %s`, plus the stop→pause write."""
    repo = TeamWorkItemRepository(mysql_client)
    team = f"{_PREFIX}_t2"
    mine = await repo.create_item(team_id=team, channel_id="ch", title="mine",
                                  created_by="agent_lead", root_run_id=f"{_PREFIX}_root")
    other = await repo.create_item(team_id=team, channel_id="ch", title="other",
                                   created_by="agent_lead", root_run_id=f"{_PREFIX}_x")

    paused = await repo.pause_by_root(f"{_PREFIX}_root")

    assert paused == 1
    assert (await repo.get(mine.item_id)).status == WorkItemStatus.PAUSED
    assert (await repo.get(other.item_id)).status == WorkItemStatus.OPEN
