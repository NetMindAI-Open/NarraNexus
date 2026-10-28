"""
@file_name: test_multipart_mysql.py
@date: 2026-09-09
@description: Real-MySQL twin for the multipart write edge — the one new raw
statement in local_bus (`_resolve_part_group`'s "sender's latest part in this
channel" lookup) plus the three nullable part columns round-tripping through
`_row_to_message`. Enable with NARRANEXUS_MYSQL_TEST_URL; skipped otherwise.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from narranexus.platform.message_bus import multipart
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that the multipart group lookup and the part columns behave on the "
        "real MySQL dialect"
    ),
)

_P = "mysqlmp"
A, B, CH = f"{_P}_a", f"{_P}_b", f"{_P}_ch"


@pytest_asyncio.fixture
async def backend():
    be = MySQLBackend(parse_mysql_url(mysql_url()))
    await be.initialize()
    await auto_migrate(be)

    async def _cleanup():
        await be.delete("bus_messages", {"channel_id": CH})
        await be.delete("bus_channel_members", {"channel_id": CH})
        await be.delete("bus_channels", {"channel_id": CH})

    await _cleanup()
    yield be
    await _cleanup()
    await be.close()


@pytest.mark.asyncio
async def test_parts_group_in_order_and_reassemble(backend):
    bus = LocalMessageBus(backend=backend)
    await backend.insert("bus_channels", {"channel_id": CH, "name": CH, "channel_type": "direct", "created_by": A})
    for agent in (A, B):
        await backend.insert("bus_channel_members", {"channel_id": CH, "agent_id": agent})

    with pytest.raises(ValueError):
        await bus.send_message(A, CH, "late", part_index=2, part_count=2)

    p1 = await bus.send_message(A, CH, "hello ", part_index=1, part_count=2)
    p2 = await bus.send_message(A, CH, "world", part_index=2, part_count=2)

    pending = await bus.get_pending_messages(B, channel_id=CH)
    assert [(m.message_id, m.part_index, m.part_count, m.part_group) for m in pending] == [
        (p1, 1, 2, p1), (p2, 2, 2, p1),
    ]
    whole, hold = multipart.assemble(pending)
    assert hold is False and [m.content for m in whole] == ["hello world"]

    # The group budget's stored-bytes lookup (second raw statement) on MySQL:
    # a part that would overflow the total is refused.
    big = "y" * (multipart.MAX_BUS_MESSAGE_BYTES - 10)
    await bus.send_message(A, CH, big, part_index=1, part_count=5)
    await bus.send_message(A, CH, big, part_index=2, part_count=5)
    await bus.send_message(A, CH, big, part_index=3, part_count=5)
    with pytest.raises(ValueError) as exc:
        await bus.send_message(A, CH, big, part_index=4, part_count=5)
    assert "two separate messages" in str(exc.value)
