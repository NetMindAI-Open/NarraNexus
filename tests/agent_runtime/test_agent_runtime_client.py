"""
@file_name: test_agent_runtime_client.py
@date: 2026-06-17
@description: AgentRuntimeClient seam — InProcess transport must be
behaviour-identical to constructing AgentRuntime + collect_run directly.

This locks the contract triggers depend on, so the later HTTP transport
(extracted agent-runtime service) can be swapped in without touching any
trigger.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime.client import (
    AgentRuntimeClient,
    InProcessAgentRuntimeClient,
    get_agent_runtime_client,
)
from xyz_agent_context.schema.runtime_message import MessageType


class _FakeMsg:
    def __init__(self, message_type, delta=None, tool_name=None):
        self.message_type = message_type
        self.delta = delta
        self.tool_name = tool_name
        self.raw = None


class _FakeRuntime:
    """Records the kwargs it was called with and yields canned events."""

    last_kwargs: dict | None = None

    def run(self, **kwargs):
        _FakeRuntime.last_kwargs = kwargs

        async def _gen():
            yield _FakeMsg(MessageType.AGENT_RESPONSE, delta="hello ")
            yield _FakeMsg(MessageType.TOOL_CALL, tool_name="web_search")
            yield _FakeMsg(MessageType.AGENT_RESPONSE, delta="world")

        return _gen()


@pytest.fixture
def patch_runtime(monkeypatch):
    _FakeRuntime.last_kwargs = None
    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.agent_runtime.AgentRuntime",
        _FakeRuntime,
    )


def test_factory_returns_a_client():
    client = get_agent_runtime_client()
    assert isinstance(client, AgentRuntimeClient)
    assert isinstance(client, InProcessAgentRuntimeClient)


@pytest.mark.asyncio
async def test_run_and_collect_delegates_and_groups(patch_runtime):
    client = InProcessAgentRuntimeClient()
    result = await client.run_and_collect(
        agent_id="agent_x",
        user_id="user_y",
        input_content="hi",
        working_source="chat",
        trigger_extra_data={"trigger_id": "t1"},
    )
    # collect_run grouping behaviour preserved.
    assert result.output_text == "hello world"
    assert "web_search" in result.tool_calls
    assert not result.is_error
    # kwargs flowed through unchanged (incl. extra_kwargs).
    assert _FakeRuntime.last_kwargs["agent_id"] == "agent_x"
    assert _FakeRuntime.last_kwargs["user_id"] == "user_y"
    assert _FakeRuntime.last_kwargs["working_source"] == "chat"
    assert _FakeRuntime.last_kwargs["trigger_extra_data"] == {"trigger_id": "t1"}


@pytest.mark.asyncio
async def test_run_stream_yields_runtime_events(patch_runtime):
    client = InProcessAgentRuntimeClient()
    deltas = []
    async for msg in client.run_stream(
        agent_id="a",
        user_id="u",
        input_content="hi",
        trigger_extra_data={"trigger_id": "sse"},
    ):
        if msg.message_type == MessageType.AGENT_RESPONSE:
            deltas.append(msg.delta)
    assert "".join(deltas) == "hello world"
    # working_source omitted by caller → not forwarded (runtime keeps its default).
    assert "working_source" not in _FakeRuntime.last_kwargs


@pytest.mark.asyncio
async def test_run_stream_forwards_working_source_when_given(patch_runtime):
    client = InProcessAgentRuntimeClient()
    agen = client.run_stream(
        agent_id="a", user_id="u", input_content="hi", working_source="chat"
    )
    async for _ in agen:
        pass
    assert _FakeRuntime.last_kwargs["working_source"] == "chat"
