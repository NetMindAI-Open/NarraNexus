"""
@file_name: test_oom_audit.py
@author: Bin Liang
@date: 2026-06-18
@description: Tests for _record_executor_infra_event — the executor-infra audit
hook in step_3_agent_loop. Retry is deferred; this only proves the
monitoring-visibility write fires for the right failure class and stays
silent / safe otherwise.

Covers the two executor-infra fatals:
  - OOM subprocess kill: exit code -9 (SIGKILL) and -6 (SIGABRT) → oom_killed
  - unreachable executor/broker (ExecutorUnreachableError) → executor_unreachable
"""
import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _record_executor_infra_event,
)
from xyz_agent_context.repository.executor_audit_repository import (
    ExecutorAuditRepository,
)


@pytest.mark.asyncio
async def test_records_oom_killed_on_minus9(db_client):
    err = "Agent execution error: Command failed with exit code -9 (exit code: -9)"
    await _record_executor_infra_event(db_client, "user1", "RuntimeError", err, False)

    rows = await ExecutorAuditRepository(db_client).recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "oom_killed"
    assert rows[0]["user_id"] == "user1"
    assert "output_already_emitted" in (rows[0]["detail_json"] or "")


@pytest.mark.asyncio
async def test_records_oom_killed_on_minus6(db_client):
    err = "AGENT-LOOP-FATAL RuntimeError: Command failed with exit code -6"
    await _record_executor_infra_event(db_client, "user1", "RuntimeError", err, False)

    rows = await ExecutorAuditRepository(db_client).recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "oom_killed"


@pytest.mark.asyncio
async def test_records_executor_unreachable(db_client):
    err = "Executor unreachable at http://nx-exec-abc:8020: ClientConnectorError"
    await _record_executor_infra_event(
        db_client, "user2", "ExecutorUnreachableError", err, True
    )

    rows = await ExecutorAuditRepository(db_client).recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "executor_unreachable"
    assert rows[0]["user_id"] == "user2"


@pytest.mark.asyncio
async def test_skips_non_infra_errors(db_client):
    # A user's provider timeout / normal non-zero exit is not executor infra.
    await _record_executor_infra_event(
        db_client, "user1", "TimeoutError", "read timed out", False
    )
    await _record_executor_infra_event(
        db_client, "user1", "RuntimeError", "Command failed with exit code 1", False
    )
    rows = await ExecutorAuditRepository(db_client).recent(limit=10)
    assert rows == []


@pytest.mark.asyncio
async def test_never_raises_on_bad_db_client():
    # A broken/None db client must never crash the agent loop's error path.
    await _record_executor_infra_event(
        None, "user1", "RuntimeError", "exit code -9", True
    )
    await _record_executor_infra_event(
        None, "user1", "ExecutorUnreachableError", "unreachable", True
    )
