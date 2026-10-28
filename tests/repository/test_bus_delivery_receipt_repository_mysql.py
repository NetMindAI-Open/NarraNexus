"""
@file_name: test_bus_delivery_receipt_repository_mysql.py
@date: 2026-09-09
@description: Real-MySQL twin for BusDeliveryReceiptRepository — the composite
key upsert (update-then-insert), the nullable reason/content_key columns and
the `prior_outcome` filter on the real dialect. Enable with
NARRANEXUS_MYSQL_TEST_URL; skipped otherwise.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from narranexus.platform.repository.bus_delivery_receipt_repository import (
    RECEIPT_ACCEPTED,
    RECEIPT_DROPPED,
    RECEIPT_SILENT,
    BusDeliveryReceiptRepository,
    content_key,
)
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that bus_delivery_receipts' composite-key upsert, nullable columns and "
        "the prior_outcome filter behave on the real MySQL dialect"
    ),
)

SENDER = "mysqlreceipt_sender"
TO = "mysqlreceipt_to"
CH = "mysqlreceipt_ch"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        await client.delete(BusDeliveryReceiptRepository.TABLE, {"from_agent": SENDER})

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


@pytest.mark.asyncio
async def test_upsert_roundtrip_and_prior_outcome(mysql_client):
    repo = BusDeliveryReceiptRepository(mysql_client)
    await repo.upsert(message_id="r1", to_agent=TO, channel_id=CH, from_agent=SENDER, status=RECEIPT_ACCEPTED)
    row = await repo.get("r1", TO)
    assert row["status"] == RECEIPT_ACCEPTED and row["reason"] is None

    await repo.upsert(
        message_id="r1", to_agent=TO, channel_id=CH, from_agent=SENDER,
        status=RECEIPT_DROPPED, reason="crashed", attempts=3,
    )
    row = await repo.get("r1", TO)
    assert (row["status"], row["reason"], row["attempts"]) == (RECEIPT_DROPPED, "crashed", 3)

    key = content_key("hello there")
    await repo.upsert(
        message_id="r2", to_agent=TO, channel_id=CH, from_agent=SENDER,
        status=RECEIPT_SILENT, content_key=key,
    )
    assert await repo.prior_outcome(channel_id=CH, to_agent=TO, key=key, status=RECEIPT_SILENT, exclude_message_ids=["r3"], within_seconds=3600) is True
    assert await repo.prior_outcome(channel_id=CH, to_agent=TO, key=key, status=RECEIPT_SILENT, exclude_message_ids=["r2"], within_seconds=3600) is False

    # Retention DELETE on the real dialect.
    from datetime import timedelta

    from narranexus.platform.utils.timezone import utc_now

    assert await repo.cleanup_older_than_days(30) == 0
    await mysql_client.update(
        BusDeliveryReceiptRepository.TABLE, {"message_id": "r1", "to_agent": TO},
        {"updated_at": utc_now() - timedelta(days=40)},
    )
    assert await repo.cleanup_older_than_days(30) == 1
