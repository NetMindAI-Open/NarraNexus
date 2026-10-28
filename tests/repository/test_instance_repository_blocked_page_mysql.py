"""
@file_name: test_instance_repository_blocked_page_mysql.py
@author: Bin Liang
@date: 2026-09-10
@description: Real-MySQL twin for `InstanceRepository.get_blocked_page`
(review r2 I-A): the `id > %s` keyset predicate, `ORDER BY id` and the
placeholder LIMIT on the real dialect; plus the correlated NOT EXISTS
narrative-link exclusion shared with `get_unlinked_blocked_by_agent` and
`get_unlinked_completed_awaiting_callback` (review r3 C1/I2). Enable with
NARRANEXUS_MYSQL_TEST_URL.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from narranexus.platform.repository import InstanceNarrativeLinkRepository, InstanceRepository
from narranexus.platform.schema.instance_schema import (
    InstanceStatus,
    LinkType,
    ModuleInstanceRecord,
)
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that the keyset page behind the BLOCKED reconciliation (id > cursor, "
        "ORDER BY id, inlined LIMIT) behaves on the real MySQL dialect"
    ),
)

_PREFIX = "blockedpage"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        await client.execute(
            "DELETE FROM module_instances WHERE instance_id LIKE %s", (f"{_PREFIX}%",), fetch=False,
        )
        await client.execute(
            "DELETE FROM instance_narrative_links WHERE instance_id LIKE %s", (f"{_PREFIX}%",),
            fetch=False,
        )

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


async def _seed(db, name, status, agent_id="agent_1"):
    await InstanceRepository(db).create_instance(ModuleInstanceRecord(
        instance_id=f"{_PREFIX}_{name}", module_class="JobModule", agent_id=agent_id,
        status=status, dependencies=["x"],
    ))


async def _mine(repo, after, limit):
    # The shared test database may hold other BLOCKED rows; keep only ours.
    return [r for r in await repo.get_blocked_page(after, limit) if r.instance_id.startswith(_PREFIX)]


@pytest.mark.asyncio
async def test_keyset_walk_on_mysql(mysql_client):
    for i in range(5):
        await _seed(mysql_client, f"b{i}", InstanceStatus.BLOCKED)
    await _seed(mysql_client, "active", InstanceStatus.ACTIVE)
    repo = InstanceRepository(mysql_client)

    first_ours = (await repo.get_by_instance_id(f"{_PREFIX}_b0")).id
    seen, after = [], first_ours - 1
    while True:
        page = await repo.get_blocked_page(after, 2)
        if not page:
            break
        assert len(page) <= 2
        assert [r.id for r in page] == sorted(r.id for r in page)
        assert all(r.id > after for r in page)
        seen += [r.instance_id for r in page if r.instance_id.startswith(_PREFIX)]
        # Rows leaving the BLOCKED set mid-walk must not shift the next page.
        if page[0].instance_id.startswith(_PREFIX):
            await repo.update_status(page[0].instance_id, InstanceStatus.ACTIVE)
        after = page[-1].id

    assert seen == [f"{_PREFIX}_b{i}" for i in range(5)]
    assert await _mine(repo, after, 10) == []


async def _link(db, name, link_type):
    await InstanceNarrativeLinkRepository(db).link(
        f"{_PREFIX}_{name}", f"{_PREFIX}_nar", link_type=link_type,
    )


def _names(rows):
    return [r.instance_id[len(_PREFIX) + 1:] for r in rows if r.instance_id.startswith(_PREFIX)]


@pytest.mark.asyncio
async def test_narrative_link_exclusion_on_mysql(mysql_client):
    await _seed(mysql_client, "free", InstanceStatus.BLOCKED)
    await _seed(mysql_client, "bound_active", InstanceStatus.BLOCKED)
    await _link(mysql_client, "bound_active", LinkType.ACTIVE)
    await _seed(mysql_client, "bound_history", InstanceStatus.BLOCKED)
    await _link(mysql_client, "bound_history", LinkType.HISTORY)
    agent = f"{_PREFIX}_agent"
    await _seed(mysql_client, "agent_free", InstanceStatus.BLOCKED, agent_id=agent)
    await _seed(mysql_client, "agent_bound", InstanceStatus.BLOCKED, agent_id=agent)
    await _link(mysql_client, "agent_bound", LinkType.ACTIVE)
    repo = InstanceRepository(mysql_client)

    walked, after = [], 0
    while True:
        page = await repo.get_blocked_page(after, 50)
        if not page:
            break
        walked += _names(page)
        after = page[-1].id
    assert walked == ["free", "agent_free"]
    assert _names(await repo.get_unlinked_blocked_by_agent(agent)) == ["agent_free"]


@pytest.mark.asyncio
async def test_unlinked_completed_awaiting_callback_on_mysql(mysql_client):
    async def _awaiting(name, status="completed", completed_at="2026-09-01 08:00:00"):
        await mysql_client.execute(
            "UPDATE module_instances SET status = %s, last_polled_status = 'in_progress', "
            "callback_processed = FALSE, completed_at = %s WHERE instance_id = %s",
            (status, completed_at, f"{_PREFIX}_{name}"), fetch=False,
        )

    await _seed(mysql_client, "done_late", InstanceStatus.ACTIVE)
    await _awaiting("done_late", completed_at="2000-01-02 08:00:00")
    await _seed(mysql_client, "failed_early", InstanceStatus.ACTIVE)
    await _awaiting("failed_early", status="failed", completed_at="2000-01-01 08:00:00")
    await _seed(mysql_client, "bound", InstanceStatus.ACTIVE)
    await _awaiting("bound", completed_at="2000-01-01 07:00:00")
    await _link(mysql_client, "bound", LinkType.ACTIVE)
    await _seed(mysql_client, "processed", InstanceStatus.ACTIVE)
    await _awaiting("processed", completed_at="2000-01-01 06:00:00")
    await mysql_client.execute(
        "UPDATE module_instances SET callback_processed = TRUE WHERE instance_id = %s",
        (f"{_PREFIX}_processed",), fetch=False,
    )
    repo = InstanceRepository(mysql_client)

    # Year-2000 completion times sort ahead of any other row in the shared DB.
    rows = await repo.get_unlinked_completed_awaiting_callback(2)
    assert _names(rows) == ["failed_early", "done_late"]
