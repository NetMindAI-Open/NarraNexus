"""
@file_name: test_policy_store_mysql.py
@author:
@date: 2026-10-08
@description: The browser permission compare-and-swap against a real MySQL dialect.

``PolicyStore.set_rule`` is the browser's only hand-written SQL: an UPDATE
guarded by ``BINARY agent_id = BINARY %s AND BINARY policy_json = BINARY %s``.
SQLite never executes that statement as written — its backend strips
``BINARY`` — so the SQLite suite says nothing about three things that only
exist here: ``BINARY`` over a MEDIUMTEXT JSON document, aiomysql's rowcount
(affected vs matched rows) as the "did my swap land" signal, and real
concurrent writers racing on one row. A wrong answer is silent: the owner
clicks "allow", the write never lands or lands over someone else's rule.

Run with a throwaway MySQL:

    docker run --rm -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root \\
        -e MYSQL_DATABASE=nxtest --name nx-mysql-test mysql:8
    export NARRANEXUS_MYSQL_TEST_URL=mysql://root:root@127.0.0.1:3306/nxtest
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, decide
from narranexus.platform.browser._browser_impl.policy_store import PolicyStore
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "the browser permission compare-and-swap, whose BINARY comparison and "
        "rowcount signal SQLite never executes"
    ),
)

_PREFIX = "mysqlbrowserpolicy"
AGENT = f"{_PREFIX}_agent"
TABLE = PolicyStore.TABLE


async def _clean(client, *, quiet: bool) -> None:
    """Remove this file's rows; ``quiet`` only on teardown (see the team-room twin)."""
    try:
        await client.execute(f"DELETE FROM {TABLE} WHERE agent_id LIKE %s", (f"{_PREFIX}%",), fetch=False)
    except Exception:  # noqa: BLE001 — teardown must not mask the result
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


async def _stored(client) -> BrowserPolicy:
    row = await client.get_one(TABLE, {"agent_id": AGENT})
    return BrowserPolicy.from_dict(json.loads(row["policy_json"]))


def _verdict(policy: BrowserPolicy, origin: str) -> str:
    return decide(policy, url=origin, capability="full_cdp_access").verdict


@pytest.mark.asyncio
async def test_a_rule_is_created_changed_and_reread(mysql_client):
    """First write inserts the row, later writes swap it; the view re-reads."""
    store = PolicyStore(mysql_client)
    view = await store.set_rule(AGENT, origin="https://a.example", capability="full_cdp_access", verdict="allow")
    assert view["origins"] == [{"origin": "https://a.example", "full_cdp_access": "allow"}]
    await store.set_rule(AGENT, origin="https://a.example", capability="full_cdp_access", verdict="deny")
    assert _verdict(await _stored(mysql_client), "https://a.example") == "deny"


@pytest.mark.asyncio
async def test_repeating_the_same_verdict_still_reports_success(mysql_client):
    """MySQL's rowcount counts CHANGED rows unless CLIENT_FOUND_ROWS is set. The
    swap also rewrites updated_at, so re-saving an identical rule must still
    count as written — not fall through to the retry path and fail."""
    store = PolicyStore(mysql_client)
    for _ in range(3):
        view = await store.set_rule(AGENT, origin="https://same.example", capability="full_cdp_access", verdict="allow")
        assert view["origins"] == [{"origin": "https://same.example", "full_cdp_access": "allow"}]


@pytest.mark.asyncio
async def test_a_document_changed_underneath_is_not_overwritten(mysql_client):
    """The guard must reject a stale snapshot byte-for-byte (BINARY), so a writer
    that read before another committed re-reads instead of erasing its rule."""
    store = PolicyStore(mysql_client)
    await store.set_rule(AGENT, origin="https://first.example", capability="full_cdp_access", verdict="allow")
    stale = await mysql_client.get_one(TABLE, {"agent_id": AGENT})
    await store.set_rule(AGENT, origin="https://second.example", capability="full_cdp_access", verdict="allow")

    lost = await mysql_client.execute(
        f"UPDATE {TABLE} SET policy_json = %s, updated_at = %s "
        "WHERE BINARY agent_id = BINARY %s AND BINARY policy_json = BINARY %s",
        (json.dumps(BrowserPolicy().to_dict()), "2026-10-08 00:00:00", AGENT, stale["policy_json"]),
        fetch=False,
    )
    assert not lost
    policy = await _stored(mysql_client)
    assert _verdict(policy, "https://first.example") == _verdict(policy, "https://second.example") == "allow"


@pytest.mark.asyncio
async def test_concurrent_writers_on_one_row_all_land(mysql_client):
    """Real concurrent connections, one row: every writer's rule survives."""
    sites = [f"https://site{i}.example" for i in range(6)]
    await PolicyStore(mysql_client).set_rule(AGENT, origin="https://seed.example",
                                             capability="full_cdp_access", verdict="deny")
    await asyncio.gather(*(
        PolicyStore(mysql_client).set_rule(AGENT, origin=site, capability="full_cdp_access",
                                           verdict="allow" if i % 2 == 0 else "deny")
        for i, site in enumerate(sites)
    ))
    policy = await _stored(mysql_client)
    assert [_verdict(policy, site) for site in sites] == ["allow", "deny"] * 3
    assert _verdict(policy, "https://seed.example") == "deny"


@pytest.mark.asyncio
async def test_cross_process_writers_race_through_the_compare_and_swap(mysql_client):
    """Writers in DIFFERENT processes do not share the in-process lock; only the
    CAS protects them. Simulated by calling the swap directly from separate
    connections: a few racing writers all land within the attempt bound."""
    await PolicyStore(mysql_client).set_rule(AGENT, origin="https://seed.example",
                                             capability="full_cdp_access", verdict="deny")
    clients = []
    try:
        for _ in range(3):
            backend = MySQLBackend(parse_mysql_url(mysql_url()))
            await backend.initialize()
            clients.append(await AsyncDatabaseClient.create_with_backend(backend))
        sites = [f"https://proc{i}.example" for i in range(len(clients))]
        await asyncio.gather(*(
            PolicyStore(client)._swap_rule(AGENT, origin=site, capability="full_cdp_access", verdict="allow")
            for client, site in zip(clients, sites)
        ))
        policy = await _stored(mysql_client)
        assert all(_verdict(policy, site) == "allow" for site in sites)
        assert _verdict(policy, "https://seed.example") == "deny"
    finally:
        for client in clients:
            await client.close()
