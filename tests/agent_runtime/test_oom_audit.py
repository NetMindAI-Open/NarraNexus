"""
@file_name: test_oom_audit.py
@author: Bin Liang
@date: 2026-06-18
@description: Tests for _record_oom_if_killed — the minimal OOM (exit code -9)
audit hook in step_3_agent_loop. Retry is deferred; this only proves the
monitoring-visibility write fires on a -9 and stays silent / safe otherwise.
"""
import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _record_oom_if_killed,
)
from xyz_agent_context.repository.executor_audit_repository import (
    ExecutorAuditRepository,
)


@pytest.mark.asyncio
async def test_records_oom_killed_on_minus9(db_client):
    err = "Agent execution error: Command failed with exit code -9 (exit code: -9)"
    await _record_oom_if_killed(db_client, "user1", err, False)

    rows = await ExecutorAuditRepository(db_client).recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "oom_killed"
    assert rows[0]["user_id"] == "user1"
    assert "output_already_emitted" in (rows[0]["detail_json"] or "")


@pytest.mark.asyncio
async def test_skips_non_oom_errors(db_client):
    await _record_oom_if_killed(db_client, "user1", "TimeoutError: initialize", False)
    rows = await ExecutorAuditRepository(db_client).recent(limit=10)
    assert rows == []


@pytest.mark.asyncio
async def test_never_raises_on_bad_db_client():
    # A broken/None db client must never crash the agent loop's error path.
    await _record_oom_if_killed(None, "user1", "exit code -9", True)
