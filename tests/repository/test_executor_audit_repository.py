"""
@file_name: test_executor_audit_repository.py
@author: Bin Liang
@date: 2026-06-18
@description: TDD tests for ExecutorAuditRepository — record/recent/counts_since.
"""
import pytest
import pytest_asyncio

from xyz_agent_context.repository.executor_audit_repository import ExecutorAuditRepository


@pytest_asyncio.fixture
async def repo(db_client):
    return ExecutorAuditRepository(db_client)


@pytest.mark.asyncio
async def test_record_and_recent(repo):
    await repo.record(
        event_type="container_started",
        user_id="u1",
        container_id="c1",
        active_loops=3,
        active_users=2,
        queue_depth=0,
        free_mem_mb=5000,
    )
    rows = await repo.recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "container_started"
    assert rows[0]["user_id"] == "u1"
    assert rows[0]["container_id"] == "c1"
    assert rows[0]["active_loops"] == 3
    assert rows[0]["active_users"] == 2
    assert rows[0]["queue_depth"] == 0
    assert rows[0]["free_mem_mb"] == 5000


@pytest.mark.asyncio
async def test_record_optional_fields_can_be_none(repo):
    """event_type is the only required field; all other fields are nullable."""
    await repo.record(event_type="oom_killed")
    rows = await repo.recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "oom_killed"
    assert rows[0]["user_id"] is None


@pytest.mark.asyncio
async def test_record_with_detail_dict(repo):
    """detail dict should be JSON-serialised into detail_json column."""
    await repo.record(
        event_type="oom_retry_ok",
        detail={"attempt": 2, "mem_mb": 4096},
    )
    rows = await repo.recent(limit=10)
    assert rows[0]["detail_json"] is not None
    assert "attempt" in rows[0]["detail_json"]


@pytest.mark.asyncio
async def test_recent_returns_newest_first(repo):
    """recent() must return rows in descending created_at order."""
    await repo.record(event_type="container_started", user_id="u1")
    await repo.record(event_type="reused", user_id="u2")
    await repo.record(event_type="culled", user_id="u3")

    rows = await repo.recent(limit=10)
    assert len(rows) == 3
    # Most recent insert (culled) should be first
    assert rows[0]["event_type"] == "culled"
    assert rows[1]["event_type"] == "reused"
    assert rows[2]["event_type"] == "container_started"


@pytest.mark.asyncio
async def test_recent_respects_limit(repo):
    for i in range(5):
        await repo.record(event_type="admit_queued", user_id=f"u{i}")
    rows = await repo.recent(limit=3)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_counts_since(repo):
    await repo.record(event_type="culled", user_id="u1")
    await repo.record(event_type="culled", user_id="u2")
    await repo.record(event_type="oom_killed", user_id="u1")
    counts = await repo.counts_since("1970-01-01T00:00:00")
    assert counts.get("culled") == 2
    assert counts.get("oom_killed") == 1


@pytest.mark.asyncio
async def test_counts_since_future_cutoff_returns_empty(repo):
    """A cutoff in the far future should exclude all rows."""
    await repo.record(event_type="culled", user_id="u1")
    counts = await repo.counts_since("2099-01-01T00:00:00")
    assert counts == {}


@pytest.mark.asyncio
async def test_counts_since_unknown_events_not_counted(repo):
    """Only events after the cutoff are counted."""
    await repo.record(event_type="orphan_reaped", user_id="u1")
    counts = await repo.counts_since("1970-01-01T00:00:00")
    assert counts.get("orphan_reaped") == 1
    assert counts.get("culled", 0) == 0
