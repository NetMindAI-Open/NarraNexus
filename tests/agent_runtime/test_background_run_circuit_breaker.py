"""
@file_name: test_background_run_circuit_breaker.py
@author:
@date: 2026-07-13
@description: BackgroundRun → Agent circuit-breaker wiring.

Verifies the outcome→breaker mapping in _record_circuit_breaker, including the
subtle case where a fatal auth/quota error ends the run NATURALLY (state ==
completed + _had_fatal_error) and must still count as a failure.
"""

import pytest

from xyz_agent_context.agent_runtime import background_run as brmod
from xyz_agent_context.agent_runtime.background_run import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    BackgroundRun,
)


def _make_run() -> BackgroundRun:
    return BackgroundRun(
        agent_id="ag_1",
        user_id="u_1",
        input_preview="hi",
        db=None,  # unused — breaker calls are monkeypatched
        active_runs={},
    )


@pytest.fixture
def spy(monkeypatch):
    calls = {"failure": [], "success": []}

    async def fake_failure(agent_id, error_type, error_message, db=None):
        calls["failure"].append((agent_id, error_type, error_message))

    async def fake_success(agent_id, db=None):
        calls["success"].append(agent_id)

    import xyz_agent_context.agent_framework.agent_circuit_breaker as cb
    monkeypatch.setattr(cb, "record_failure", fake_failure)
    monkeypatch.setattr(cb, "record_success", fake_success)
    return calls


@pytest.mark.asyncio
async def test_failed_run_records_failure(spy):
    run = _make_run()
    run.state = STATE_FAILED
    run._last_error_type = "TimeoutError"
    run._last_error_message = "read timed out"
    await run._record_circuit_breaker()
    assert spy["failure"] == [("ag_1", "TimeoutError", "read timed out")]
    assert spy["success"] == []


@pytest.mark.asyncio
async def test_fatal_completed_run_records_failure(spy):
    # Dead key: the generator ended naturally (STATE_COMPLETED) but a fatal
    # error was emitted — must count as a failure, not a success.
    run = _make_run()
    run.state = STATE_COMPLETED
    run._had_fatal_error = True
    run._last_error_type = "auth_expired"
    run._last_error_message = "login expired"
    await run._record_circuit_breaker()
    assert spy["failure"] == [("ag_1", "auth_expired", "login expired")]
    assert spy["success"] == []


@pytest.mark.asyncio
async def test_clean_completion_records_success(spy):
    run = _make_run()
    run.state = STATE_COMPLETED
    run._had_fatal_error = False
    await run._record_circuit_breaker()
    assert spy["success"] == ["ag_1"]
    assert spy["failure"] == []


@pytest.mark.asyncio
async def test_cancelled_run_touches_nothing(spy):
    run = _make_run()
    run.state = STATE_CANCELLED
    await run._record_circuit_breaker()
    assert spy["failure"] == []
    assert spy["success"] == []


@pytest.mark.asyncio
async def test_breaker_error_never_propagates(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    import xyz_agent_context.agent_framework.agent_circuit_breaker as cb
    monkeypatch.setattr(cb, "record_failure", boom)

    run = _make_run()
    run.state = STATE_FAILED
    run._last_error_message = "x"
    # Must NOT raise — the breaker is an observer.
    await run._record_circuit_breaker()
