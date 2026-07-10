"""
@file_name: test_silent_mode.py
@author: NetMind.AI
@date: 2026-07-02
@description: AgentRuntime.run(silent=True) contract — signature check +
kwarg propagation through the client seam.

Silent mode is used by IM triggers (Matrix / Lark / Slack) to run the
memory-only path for group non-@ messages and reconnect burst backfill:
narrative selection, module load, instance sync, hook_persist_turn,
step_5 hooks all fire; step_3 (agent LLM) is skipped. See
`AgentRuntime.run(silent=True)` docstring for the full contract.

This file locks the two seams that would silently break if refactored:

1. `AgentRuntime.run` MUST accept `silent: bool = False` as a keyword
   parameter, defaulting to False (owner-facing byte-identical).
2. The `AgentRuntimeClient` in-process transport MUST forward the
   `silent` kwarg verbatim to `AgentRuntime.run` — the client protocol
   relies on `**extra_kwargs` passthrough (see client.py module
   docstring), and a stray filter here would silently degrade IM
   triggers to full agent runs (10× cost regression).
"""
from __future__ import annotations

import inspect

import pytest

from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
from xyz_agent_context.agent_runtime.client import InProcessAgentRuntimeClient
from xyz_agent_context.schema.runtime_message import MessageType


class _FakeMsg:
    def __init__(self, message_type, delta=None, tool_name=None):
        self.message_type = message_type
        self.delta = delta
        self.tool_name = tool_name
        self.raw = None


class _FakeRuntime:
    """Records the kwargs it was called with and yields one canned event."""

    last_kwargs: dict | None = None

    def run(self, **kwargs):
        _FakeRuntime.last_kwargs = kwargs

        async def _gen():
            yield _FakeMsg(MessageType.AGENT_RESPONSE, delta="")

        return _gen()


@pytest.fixture
def patch_runtime(monkeypatch):
    _FakeRuntime.last_kwargs = None
    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.agent_runtime.AgentRuntime",
        _FakeRuntime,
    )


def test_agent_runtime_run_accepts_silent_kwarg():
    """Contract: `silent` is a keyword-only opt-in, default False.

    A default of True would flip owner-facing runs to silent mode and
    silently drop replies — treat this as a landmine and lock it here.
    """
    sig = inspect.signature(AgentRuntime.run)
    assert "silent" in sig.parameters, "silent kwarg missing from AgentRuntime.run"
    assert sig.parameters["silent"].default is False, (
        "silent MUST default to False so owner-facing runs are unchanged"
    )


@pytest.mark.asyncio
async def test_silent_kwarg_flows_through_client_to_runtime(patch_runtime):
    """The in-process client MUST forward `silent=True` to AgentRuntime.run.

    Triggers call `client.run_and_collect(..., silent=True)` for the
    silent-batch path. If the client filters or drops that kwarg, every
    non-@ group message would spawn a full agent-loop run — the exact
    cost regression this mode was built to avoid.
    """
    client = InProcessAgentRuntimeClient()
    await client.run_and_collect(
        agent_id="agent_x",
        user_id="user_y",
        input_content="[t1] Alice: hi\n[t2] Bob: yo",
        working_source="narramessenger",
        trigger_extra_data={"batch_messages": [{"content": "hi"}]},
        silent=True,
    )
    assert _FakeRuntime.last_kwargs is not None
    assert _FakeRuntime.last_kwargs.get("silent") is True, (
        "silent=True kwarg did not reach AgentRuntime.run through the client seam"
    )
    # batch_messages travels inside trigger_extra_data — verify it survived.
    assert _FakeRuntime.last_kwargs["trigger_extra_data"] == {
        "batch_messages": [{"content": "hi"}]
    }


@pytest.mark.asyncio
async def test_silent_omitted_defaults_to_non_silent(patch_runtime):
    """The default owner-facing call MUST NOT set silent=True.

    A trigger that never mentions `silent` should reach AgentRuntime with
    silent absent (or explicitly False). This locks the "opt-in only"
    contract so no future refactor sneaks silent=True in as a default.
    """
    client = InProcessAgentRuntimeClient()
    await client.run_and_collect(
        agent_id="a", user_id="u", input_content="hi", working_source="chat",
    )
    assert _FakeRuntime.last_kwargs is not None
    # Either absent, or explicitly False. Never True.
    assert _FakeRuntime.last_kwargs.get("silent", False) is False
