"""
@file_name: test_instance_link_race.py
@author: Bin Liang
@date: 2026-06-09
@description: InstanceNarrativeLinkRepository.link must be race-safe.

Regression: link() did find_one()-then-insert(). Under concurrency (the
JobTrigger poller and a chat turn both syncing the same agent↔narrative — common
once an agent has many jobs, e.g. an imported squad) two runs both passed the
find_one "not linked" check and both inserted, so the second tripped the
composite UNIQUE(instance_id, narrative_id) and surfaced as
"SQLite Proxy error (/insert): UNIQUE constraint failed". The lost-race insert is
now caught and treated as "already linked" (return 0).
"""
import pytest

from xyz_agent_context.repository.instance_link_repository import (
    InstanceNarrativeLinkRepository,
)
from xyz_agent_context.schema.instance_schema import LinkType


async def _seed(db, inst="inst_x", nar="nar_x"):
    await db.insert("module_instances", {
        "instance_id": inst, "module_class": "ChatModule",
        "agent_id": "agent_x", "user_id": "u", "status": "active",
    })
    await db.insert("narratives", {
        "narrative_id": nar, "type": "chat", "agent_id": "agent_x", "round_counter": 0,
    })


@pytest.mark.asyncio
async def test_link_is_idempotent(db_client):
    await _seed(db_client)
    repo = InstanceNarrativeLinkRepository(db_client)

    first = await repo.link("inst_x", "nar_x")
    second = await repo.link("inst_x", "nar_x")  # same pair again

    assert first != 0, "first link inserts a new row"
    assert second == 0, "second link is a no-op (already exists)"
    rows = await db_client.get("instance_narrative_links", {"instance_id": "inst_x"})
    assert len(rows) == 1, "exactly one link row, no duplicate"


@pytest.mark.asyncio
async def test_link_survives_lost_insert_race(db_client, monkeypatch):
    """Simulate the race: find_one sees nothing (stale read) but the row already
    exists, so the insert hits the UNIQUE constraint. link() must swallow THAT
    specific collision and return 0 instead of erroring."""
    await _seed(db_client)
    repo = InstanceNarrativeLinkRepository(db_client)
    await repo.link("inst_x", "nar_x")  # the concurrent winner already inserted

    async def _stale_find_one(_filters):
        return None  # pretend we didn't see the existing link (the race window)

    monkeypatch.setattr(repo, "find_one", _stale_find_one)

    result = await repo.link("inst_x", "nar_x")  # must NOT raise

    assert result == 0, "lost-race insert is treated as already-linked"
    rows = await db_client.get("instance_narrative_links", {"instance_id": "inst_x"})
    assert len(rows) == 1, "still exactly one row"


@pytest.mark.asyncio
async def test_link_reraises_unrelated_insert_errors(db_client, monkeypatch):
    """A non-duplicate insert failure must NOT be swallowed."""
    await _seed(db_client)
    repo = InstanceNarrativeLinkRepository(db_client)

    async def _find_none(_filters):
        return None

    async def _boom(_entity):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(repo, "find_one", _find_none)
    monkeypatch.setattr(repo, "insert", _boom)

    with pytest.raises(RuntimeError, match="disk I/O error"):
        await repo.link("inst_x", "nar_x")
