"""
@file_name: test_module_poller_circuit_breaker_gate.py
@author:
@date: 2026-07-13
@description: ModulePoller._execute_callback circuit-breaker skip-gate.

This Path-A trigger is currently dormant (Path B / JobTrigger is active), so
the gate is defensive. The test locks the wiring: a paused agent short-circuits
_execute_callback before it constructs an AgentRuntime.
"""
import pytest

import xyz_agent_context.agent_framework.agent_circuit_breaker as cb
from xyz_agent_context.services.module_poller import ModulePoller


@pytest.mark.asyncio
async def test_execute_callback_skips_paused_agent(monkeypatch):
    async def fake_skip(agent_id, db=None):
        return (True, "paused:auth")
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    # If the gate fails to short-circuit, this import target would blow up the
    # dormant path; guard it so a regression (gate removed) is a hard failure.
    def _boom(*a, **k):
        raise AssertionError("AgentRuntime must NOT be constructed for a paused agent")
    monkeypatch.setattr("xyz_agent_context.agent_runtime.AgentRuntime", _boom, raising=False)

    poller = ModulePoller.__new__(ModulePoller)
    # Should return cleanly without constructing a runtime.
    await poller._execute_callback(
        agent_id="ag_paused",
        user_id="u",
        narrative_id="n",
        instance_id="i",
        trigger_data={},
    )
