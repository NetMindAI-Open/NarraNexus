"""
@file_name: test_module_poller_circuit_breaker_gate.py
@author:
@date: 2026-07-13
@description: ModulePoller._execute_callback circuit-breaker gate.

This Path-A trigger is currently dormant (Path B / JobTrigger is active), so
the gate is defensive. The tests lock the wiring against the real breaker
(sqlite): an agent that is paused OR probing short-circuits _execute_callback
before it constructs an AgentRuntime, and the path never claims the half-open
probe — it cannot settle one (``_execute_callback_instance`` reports no
outcome), and an unsettled claim re-runs a dead credential every grant window
forever (#394 review C1). A healthy agent runs.
"""
from datetime import timedelta

import pytest

from narranexus.platform.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from narranexus.platform.schema import CbStatus, PausedReason
from narranexus.platform.services.module_poller import ModulePoller
from narranexus.platform.utils.timezone import utc_now


@pytest.fixture
def runtime_spy(monkeypatch, db_client):
    async def _db():
        return db_client
    monkeypatch.setattr("narranexus.platform.services.module_poller.get_db_client", _db)

    built = []

    class FakeRuntime:
        def __init__(self):
            built.append(self)

        async def _execute_callback_instance(self, **kw):
            pass
    monkeypatch.setattr("narranexus.platform.agent_runtime.AgentRuntime", FakeRuntime, raising=False)
    return built


async def _callback(agent_id: str) -> None:
    poller = ModulePoller.__new__(ModulePoller)
    await poller._execute_callback(
        agent_id=agent_id, user_id="u", narrative_id="n", instance_id="i", trigger_data={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,cooldown", [
    (CbStatus.PAUSED.value, timedelta(minutes=5)),
    # The half-open window is OPEN: a claiming entry would take the probe
    # here. This one must not — it could never settle it.
    (CbStatus.PAUSED.value, timedelta(seconds=-1)),
    (CbStatus.PROBING.value, timedelta(minutes=5)),
])
async def test_execute_callback_never_runs_or_claims_while_paused(
    runtime_spy, db_client, status, cooldown
):
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("ag_held", {
        "cb_status": status,
        "paused_reason": PausedReason.AUTH.value,
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() + cooldown,
        "probe_token": "someone" if status == CbStatus.PROBING.value else None,
    })
    await _callback("ag_held")
    assert runtime_spy == []
    row = await repo.get("ag_held")
    assert row.cb_status == status  # no claim taken
    assert row.probe_token == ("someone" if status == CbStatus.PROBING.value else None)


@pytest.mark.asyncio
async def test_execute_callback_runs_a_healthy_agent(runtime_spy, db_client):
    await _callback("ag_ok")  # no breaker row at all
    assert len(runtime_spy) == 1

