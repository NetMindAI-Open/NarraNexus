"""
@file_name: test_background_run_circuit_breaker.py
@author:
@date: 2026-07-13
@description: BackgroundRun → Agent circuit-breaker wiring.

Verifies the outcome→breaker mapping in _record_circuit_breaker, including the
subtle case where a fatal auth/quota error ends the run NATURALLY (state ==
completed + recorder.had_fatal_error) and must still count as a failure.
"""

import pytest

from narranexus.platform.agent_runtime import background_run as brmod
from narranexus.platform.agent_runtime.background_run import (
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
    calls = {"failure": [], "success": [], "release": []}

    async def fake_failure(agent_id, error_type, error_message, db=None, *, probe_token=None):
        calls["failure"].append((agent_id, error_type, error_message))
        calls["token"] = probe_token

    async def fake_success(agent_id, db=None, *, probe_token=None):
        calls["success"].append(agent_id)
        calls["token"] = probe_token

    async def fake_release(agent_id, probe_token, db=None):
        calls["release"].append(agent_id)
        calls["token"] = probe_token
        return True

    import narranexus.platform.agent_framework.loop.circuit_breaker as cb
    monkeypatch.setattr(cb, "record_failure", fake_failure)
    monkeypatch.setattr(cb, "record_success", fake_success)
    monkeypatch.setattr(cb, "release_probe", fake_release)
    return calls


@pytest.mark.asyncio
async def test_failed_run_records_failure(spy):
    run = _make_run()
    run.state = STATE_FAILED
    run.recorder.last_error_type = "TimeoutError"
    run.recorder.last_error_message = "read timed out"
    await run._record_circuit_breaker()
    assert spy["failure"] == [("ag_1", "TimeoutError", "read timed out")]
    assert spy["success"] == []


@pytest.mark.asyncio
async def test_fatal_completed_run_records_failure(spy):
    # Dead key: the generator ended naturally (STATE_COMPLETED) but a fatal
    # error was emitted — must count as a failure, not a success.
    run = _make_run()
    run.state = STATE_COMPLETED
    run.recorder.had_fatal_error = True
    run.recorder.last_error_type = "auth_expired"
    run.recorder.last_error_message = "login expired"
    await run._record_circuit_breaker()
    assert spy["failure"] == [("ag_1", "auth_expired", "login expired")]
    assert spy["success"] == []


@pytest.mark.asyncio
async def test_clean_completion_records_success(spy):
    run = _make_run()
    run.state = STATE_COMPLETED
    run.recorder.had_fatal_error = False
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

    import narranexus.platform.agent_framework.loop.circuit_breaker as cb
    monkeypatch.setattr(cb, "record_failure", boom)

    run = _make_run()
    run.state = STATE_FAILED
    run.recorder.last_error_message = "x"
    # Must NOT raise — the breaker is an observer.
    await run._record_circuit_breaker()


@pytest.mark.asyncio
async def test_cancelled_run_releases_a_probe_without_touching_the_streak(spy):
    """A user-cancelled turn is not the agent's fault (no failure, no
    success) — but if it was the half-open probe, the claim is released so
    the row does not sit PROBING until its grant expires."""
    run = _make_run()
    run.state = STATE_CANCELLED
    await run._record_circuit_breaker()
    assert spy["failure"] == []
    assert spy["success"] == []
    assert spy["release"] == ["ag_1"]


@pytest.mark.asyncio
async def test_every_settlement_carries_the_runs_probe_token(spy):
    """The claim's identity rides the run: record_failure / record_success /
    release_probe all receive the token the entry point won (#394 I1)."""
    for state, fatal in ((STATE_FAILED, False), (STATE_COMPLETED, False),
                         (STATE_CANCELLED, False)):
        run = BackgroundRun(agent_id="ag_1", user_id="u_1", input_preview="hi",
                            db=None, active_runs={}, probe_token="tok-1")
        run.state = state
        run.recorder.had_fatal_error = fatal
        spy["token"] = None
        await run._record_circuit_breaker()
        assert spy["token"] == "tok-1", state


@pytest.mark.asyncio
async def test_only_the_claiming_run_settles_the_probe(db_client, monkeypatch):
    """Against the real breaker: an ordinary run completing while another
    run holds the probe leaves the row PROBING; the claiming run's success
    closes it."""
    import narranexus.platform.agent_framework.loop.circuit_breaker as cb
    from datetime import timedelta

    from narranexus.platform.repository.agent_circuit_breaker_repository import (
        AgentCircuitBreakerRepository,
    )
    from narranexus.platform.schema import CbStatus, PausedReason
    from narranexus.platform.utils.timezone import utc_now

    async def _db():
        return db_client
    monkeypatch.setattr(cb, "get_db_client", _db)
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("ag_1", {
        "cb_status": CbStatus.PAUSED.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": "auth",
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() - timedelta(seconds=1),
    })
    adm = await cb.try_begin_probe("ag_1", db=db_client)
    assert adm.probe_token

    ordinary = _make_run()
    ordinary.state = STATE_COMPLETED
    await ordinary._record_circuit_breaker()
    assert (await repo.get("ag_1")).cb_status == CbStatus.PROBING.value

    probe = BackgroundRun(agent_id="ag_1", user_id="u_1", input_preview="hi",
                          db=db_client, active_runs={}, probe_token=adm.probe_token)
    probe.state = STATE_COMPLETED
    await probe._record_circuit_breaker()
    assert (await repo.get("ag_1")).cb_status == CbStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_an_output_budget_probe_is_released_without_verdict(db_client, monkeypatch):
    """#396 x #394: a probe run that ends fatal on output-budget exhaustion
    (drive() lands it STATE_FAILED, error_type ``output_budget_exhausted``)
    is exempt from the breaker — it must neither re-PAUSE with a doubled
    delay (a failed-probe verdict) nor close the breaker (a success). The
    claim is handed back: PAUSED, same streak, token cleared."""
    import narranexus.platform.agent_framework.loop.circuit_breaker as cb
    from datetime import timedelta

    from narranexus.platform.repository.agent_circuit_breaker_repository import (
        AgentCircuitBreakerRepository,
    )
    from narranexus.platform.schema import (
        OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE,
        CbStatus,
        PausedReason,
    )
    from narranexus.platform.utils.timezone import utc_now

    async def _db():
        return db_client
    monkeypatch.setattr(cb, "get_db_client", _db)
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("ag_1", {
        "cb_status": CbStatus.PAUSED.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": "auth",
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() - timedelta(seconds=1),
    })
    adm = await cb.try_begin_probe("ag_1", db=db_client)
    assert adm.probe_token

    probe = BackgroundRun(agent_id="ag_1", user_id="u_1", input_preview="hi",
                          db=db_client, active_runs={}, probe_token=adm.probe_token)
    probe.state = STATE_FAILED
    probe.recorder.had_fatal_error = True
    probe.recorder.last_error_type = OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE
    probe.recorder.last_error_message = "thinking exhausted the output budget"
    await probe._record_circuit_breaker()
    row = await repo.get("ag_1")
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.consecutive_failure_count == 3
    assert row.probe_token is None
