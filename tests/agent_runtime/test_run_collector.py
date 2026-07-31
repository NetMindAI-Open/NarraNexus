"""
@file_name: test_run_collector.py
@author: Bin Liang
@date: 2026-04-20
@description: Unit tests for the `collect_run` helper (Bug 2 step 2/4).

`collect_run` is the single collection loop used by every non-WS consumer
of `AgentRuntime.run()` (LarkTrigger, JobTrigger, MessageBusTrigger,
ChatTrigger A2A). It groups messages by type — text deltas, tool calls,
errors, raw payloads — so each trigger only has to implement its own
"what to do with an error" policy, not re-implement the loop.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from xyz_agent_context.agent_runtime.run_collector import (
    RunCollection,
    RunError,
    collect_run,
)
from xyz_agent_context.schema.runtime_message import AgentThinking, MessageType


class _FakeRuntime:
    """Stands in for AgentRuntime — any sequence of messages we want to
    feed collect_run."""

    def __init__(self, messages: list):
        self._messages = messages

    def run(self, **_kwargs) -> AsyncIterator:
        async def _gen():
            for m in self._messages:
                yield m
        return _gen()


def _delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        message_type=MessageType.AGENT_RESPONSE,
        delta=text,
    )


def _tool(name: str, raw: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        message_type=MessageType.TOOL_CALL,
        tool_name=name,
        tool_input={},
        raw=raw,
    )


def _error(error_type: str, message: str) -> SimpleNamespace:
    return SimpleNamespace(
        message_type=MessageType.ERROR,
        error_type=error_type,
        error_message=message,
    )


@pytest.mark.asyncio
async def test_collects_text_deltas_in_order():
    runtime = _FakeRuntime([_delta("Hello "), _delta("world"), _delta("!")])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="chat",
    )
    assert result.output_text == "Hello world!"
    assert result.error is None
    assert result.is_error is False


@pytest.mark.asyncio
async def test_collects_tool_call_names():
    runtime = _FakeRuntime([
        _delta("Let me search."),
        _tool("web_search"),
        _tool("lark_cli"),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="job",
    )
    assert result.tool_calls == ["web_search", "lark_cli"]
    assert result.output_text == "Let me search."


@pytest.mark.asyncio
async def test_collects_raw_payloads():
    """Lark's `_extract_lark_reply` needs access to the underlying raw
    dict from tool_call_item events; collect_run must preserve them."""
    raw = {"item": {"type": "tool_call_item", "input": {"command": "im +messages-send --text hi"}}}
    runtime = _FakeRuntime([_tool("lark_cli", raw=raw)])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert result.raw_items == [raw]


@pytest.mark.asyncio
async def test_error_is_captured_not_silently_dropped():
    runtime = _FakeRuntime([
        _delta("Attempting..."),
        _error("SystemDefaultUnavailable", "quota exhausted"),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert result.is_error is True
    assert result.error == RunError(
        error_type="SystemDefaultUnavailable",
        error_message="quota exhausted",
    )
    # Preserves any text that was emitted BEFORE the error — callers
    # can decide whether to show it (it's half a reply) or drop it.
    assert result.output_text == "Attempting..."


@pytest.mark.asyncio
async def test_last_error_wins_when_multiple():
    """Unusual but possible: two ERROR events. Keep the last one so the
    caller sees the most specific failure."""
    runtime = _FakeRuntime([
        _error("GenericError", "first"),
        _error("SystemDefaultUnavailable", "specific"),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="job",
    )
    assert result.error.error_type == "SystemDefaultUnavailable"
    assert result.error.error_message == "specific"


@pytest.mark.asyncio
async def test_empty_run_returns_empty_collection():
    runtime = _FakeRuntime([])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="chat",
    )
    assert result == RunCollection(
        output_text="", tool_calls=[], raw_items=[], error=None,
    )


def _progress_tool(tool_name: str, arguments: dict) -> SimpleNamespace:
    """A ProgressMessage-shaped tool_call event (what response_processor emits).

    Key property: NO `raw` attribute. collect_run used to rely on `raw` being
    populated, which made it silently drop every tool call — breaking Lark's
    inbox extraction (Bug 10 regression).
    """
    return SimpleNamespace(
        message_type=MessageType.PROGRESS,
        details={"tool_name": tool_name, "arguments": arguments},
    )


@pytest.mark.asyncio
async def test_tool_call_progress_message_synthesizes_raw_item():
    """Regression: Bug 10 kept showing `(Replied on Lark)` in the inbox because
    `raw_items` was always empty — tool call events arrive as ProgressMessage
    with `details.tool_name`, not as typed TOOL_CALL messages with `.raw`.
    Collector must synthesize the raw item shape Lark's extractor expects."""
    runtime = _FakeRuntime([
        _progress_tool(
            "mcp__lark_module__lark_cli",
            {"command": "im +messages-send --chat-id oc_x --markdown hello"},
        ),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert len(result.raw_items) == 1
    item = result.raw_items[0]["item"]
    assert item["type"] == "tool_call_item"
    assert item["tool_name"] == "mcp__lark_module__lark_cli"
    assert item["arguments"]["command"].endswith("hello")
    assert "mcp__lark_module__lark_cli" in result.tool_calls


@pytest.mark.asyncio
async def test_duplicate_tool_call_events_are_deduped():
    """With include_partial_messages=True, the same ToolUseBlock can surface
    across multiple AssistantMessage frames. Observed symptom: Lark inbox
    row showed the reply text twice. Collector must dedup by (name, args)."""
    args = {"command": "im +messages-send --chat-id oc_x --markdown ok"}
    runtime = _FakeRuntime([
        _progress_tool("mcp__lark_module__lark_cli", args),
        _progress_tool("mcp__lark_module__lark_cli", args),
        _progress_tool("mcp__lark_module__lark_cli", args),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert len(result.raw_items) == 1
    assert result.tool_calls == ["mcp__lark_module__lark_cli"]


@pytest.mark.asyncio
async def test_distinct_tool_calls_are_kept_separate():
    """Dedup must not collapse genuinely different calls (different args)."""
    runtime = _FakeRuntime([
        _progress_tool(
            "mcp__lark_module__lark_cli",
            {"command": "im +messages-send --chat-id oc_x --text first"},
        ),
        _progress_tool(
            "mcp__lark_module__lark_cli",
            {"command": "im +messages-send --chat-id oc_x --text second"},
        ),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert len(result.raw_items) == 2


@pytest.mark.asyncio
async def test_progress_message_without_tool_name_is_not_a_tool_call():
    """ProgressMessage is also used for every step/phase banner. Must not
    synthesize raw_items for those — only when details.tool_name is present."""
    step_banner = SimpleNamespace(
        message_type=MessageType.PROGRESS,
        details={"status": "running"},
    )
    runtime = _FakeRuntime([step_banner])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert result.raw_items == []
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_nexus_monologue_reaches_output_text():
    """NexusPower parity: the agent's plain text streams as AGENT_THINKING
    (monologue) — never as AGENT_RESPONSE. Triggers that relay
    ``output_text`` (bus team rooms, IM inbox relays) must receive it,
    exactly as they receive the claude driver's assistant text.

    Regression: a team-room @mention produced a perfect reply in
    final_output, but output_text stayed empty and the room got nothing
    (dev evt_238abc4b0b0c4dca, 2026-07-30)."""
    runtime = _FakeRuntime([
        AgentThinking(thinking_content="Reply part one. ",
                      monologue="Reply part one. "),
        AgentThinking(thinking_content="Reply part two.",
                      monologue="Reply part two."),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="message_bus",
        include_monologue=True,
    )
    assert result.output_text == "Reply part one. Reply part two."


@pytest.mark.asyncio
async def test_monologue_excluded_unless_opted_in():
    """Privacy guard: only surfaces whose PROMPT told the agent "your
    plain text is delivered" (team rooms) may opt in. Everywhere else
    the constitution's promise — plain text is private — must hold, or
    a peer-DM inbox / A2A response would expose internal deliberation
    the agent believed nobody would read."""
    runtime = _FakeRuntime([
        AgentThinking(thinking_content="private deliberation",
                      monologue="private deliberation"),
        _delta("polished reply"),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="message_bus",
    )
    assert result.output_text == "polished reply"


@pytest.mark.asyncio
async def test_provider_cot_thinking_stays_out_of_output_text():
    """Provider chain-of-thought (claude thinking blocks, DeepSeek CoT)
    arrives as AGENT_THINKING with an empty ``monologue`` — it must NOT
    leak into output_text, or IM relays would post raw reasoning."""
    runtime = _FakeRuntime([
        AgentThinking(thinking_content="Let me reason about this..."),
        _delta("The answer."),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert result.output_text == "The answer."


@pytest.mark.asyncio
async def test_monologue_and_response_deltas_interleave_in_order():
    """A coalesced batch can mix monologue with AGENT_RESPONSE deltas
    (e.g. a mid-run expressive reply); arrival order must be preserved."""
    runtime = _FakeRuntime([
        AgentThinking(thinking_content="First. ", monologue="First. "),
        _delta("Second. "),
        AgentThinking(thinking_content="Third.", monologue="Third."),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="message_bus",
        include_monologue=True,
    )
    assert result.output_text == "First. Second. Third."


@pytest.mark.asyncio
async def test_monologue_frame_reports_response_progress_kind():
    """The team activity view mirrors on_progress; a monologue frame is
    the agent SPEAKING (its text is about to be posted to the room), so
    it must read as "response", not "thinking"."""
    seen: list[str] = []

    async def on_progress(kind: str, _tool) -> None:
        seen.append(kind)

    runtime = _FakeRuntime([
        AgentThinking(thinking_content="cot only"),
        AgentThinking(thinking_content="the reply", monologue="the reply"),
    ])
    await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="message_bus",
        include_monologue=True, on_progress=on_progress,
    )
    assert seen == ["thinking", "response"]


@pytest.mark.asyncio
async def test_monologue_frame_stays_thinking_without_opt_in():
    """On surfaces where monologue is private (no opt-in), nobody receives
    that text — reporting "response" would show 'replying' in an activity
    view while nothing ever lands. It stays "thinking"."""
    seen: list[str] = []

    async def on_progress(kind: str, _tool) -> None:
        seen.append(kind)

    runtime = _FakeRuntime([
        AgentThinking(thinking_content="the reply", monologue="the reply"),
    ])
    await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="message_bus",
        on_progress=on_progress,
    )
    assert seen == ["thinking"]


@pytest.mark.asyncio
async def test_kwargs_forwarded_to_runtime_run():
    """Triggers pass trigger_extra_data / job_instance_id / forced_narrative_id
    etc. — collect_run must forward them verbatim."""
    seen: dict = {}

    class _CapturingRuntime:
        def run(self, **kwargs):
            seen.update(kwargs)
            async def _gen():
                if False:
                    yield None  # make this a generator
            return _gen()

    await collect_run(
        _CapturingRuntime(),
        agent_id="a", user_id="u",
        input_content="hi", working_source="job",
        job_instance_id="inst_42",
        forced_narrative_id="nar_7",
        trigger_extra_data={"k": "v"},
    )
    assert seen["agent_id"] == "a"
    assert seen["user_id"] == "u"
    assert seen["input_content"] == "hi"
    assert seen["working_source"] == "job"
    assert seen["job_instance_id"] == "inst_42"
    assert seen["forced_narrative_id"] == "nar_7"
    assert seen["trigger_extra_data"] == {"k": "v"}
