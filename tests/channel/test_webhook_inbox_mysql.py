"""
@file_name: test_webhook_inbox_mysql.py
@author: Bin Liang
@date: 2026-09-07
@description: MySQL twin for the raw SQL in WebhookInbox.purge_claimed (DELETE by cutoff on a DATETIME(6) column) and the atomic claim UPDATE's rowcount: exercised on the dialect they can break on.

    docker run --rm -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=nxtest --name nx-mysql-test mysql:8
    export NARRANEXUS_MYSQL_TEST_URL=mysql://root:root@127.0.0.1:3306/nxtest
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio

from narranexus.platform.channel.webhook_inbox import TABLE, WebhookInbox
from narranexus.platform.utils import utc_now
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(not mysql_configured(), reason=skip_reason("webhook inbox raw SQL (purge by cutoff, atomic claim rowcount)"))

CHANNEL = "mysql_wh_twin"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    await client.delete(TABLE, {"channel": CHANNEL})
    yield client
    await client.delete(TABLE, {"channel": CHANNEL})
    await client.close()


@pytest.mark.asyncio
async def test_purge_by_cutoff_and_atomic_claim_on_mysql(mysql_client):
    inbox = WebhookInbox(mysql_client)
    for i in range(3):
        await inbox.push(CHANNEL, "a1", {"id": i})
    await inbox.push(CHANNEL, "a2", {"id": 9})
    claimed = await inbox.pull(CHANNEL, "a1")
    assert [e.payload["id"] for e in claimed] == [0, 1, 2]
    # a second pull finds nothing (claim UPDATE with claimed_at IS NULL returned 0 rows)
    assert await inbox.pull(CHANNEL, "a1") == []
    assert await inbox.purge_claimed(CHANNEL, utc_now() - timedelta(days=1)) == 0
    assert await inbox.purge_claimed(CHANNEL, utc_now() + timedelta(seconds=5)) == 3
    assert await inbox.pending(CHANNEL, "a2") == 1  # unclaimed rows are never purged
