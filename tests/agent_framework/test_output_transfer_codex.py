"""
@file_name: test_output_transfer_codex.py
@date: 2026-05-29
@description: Tests for the codex_cli transfer_type branch in
output_transfer.py.

Each test takes one Codex JSON event (dict) and asserts the
post-translation event(s) match the OpenAI-Agents-SDK shape that
response_processor.ResponseProcessor.process consumes.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import (
    ResponseProcessor,
    ResponseType,
)
from xyz_agent_context.agent_framework.output_transfer import output_transfer


def _t(event: dict) -> list[dict]:
    return output_transfer(event, transfer_type="codex_cli")


# ---------------- Info-only / drop ---------------------------------


def test_thread_started_dropped():
    assert _t({"type": "thread.started", "thread_id": "abc"}) == []


def test_turn_started_dropped():
    assert _t({"type": "turn.started"}) == []


def test_unknown_event_type_dropped():
    """Forward-compat: novel events from future Codex versions
    should not raise, just be dropped quietly."""
    assert _t({"type": "novel.from.future"}) == []


def test_non_dict_input_returns_empty():
    """Defensive: a stringified line that parses as a non-dict
    (e.g. ``"hello"`` or ``[]``) should not crash."""
    assert output_transfer("a-string", transfer_type="codex_cli") == []
    assert output_transfer([], transfer_type="codex_cli") == []


# ---------------- Text from agent_message --------------------------


def test_item_started_agent_message_empty_text_dropped():
    """Codex emits item.started with empty text; the full text
    only arrives on item.completed. We drop the started entirely
    to avoid double-rendering an empty delta."""
    evs = _t({
        "type": "item.started",
        "item": {"type": "agent_message", "id": "i1", "text": ""},
    })
    assert evs == []


def test_item_completed_agent_message_emits_raw_response():
    evs = _t({
        "type": "item.completed",
        "item": {"type": "agent_message", "id": "i1", "text": "hello world"},
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "raw_response_event"
    assert evs[0]["data"]["type"] == "response.text.delta"
    assert evs[0]["data"]["delta"] == "hello world"


# ---------------- Reasoning ----------------------------------------


def test_item_completed_reasoning_emits_thinking_delta():
    evs = _t({
        "type": "item.completed",
        "item": {"type": "reasoning", "id": "i2", "text": "let me think"},
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "run_item_stream_event"
    assert evs[0]["item"]["type"] == "thinking_item"
    assert evs[0]["item"]["content"] == "let me think"


# ---------------- Tool calls (command_execution / mcp_tool_call / web_search)


def test_item_started_command_execution_emits_tool_call_item():
    evs = _t({
        "type": "item.started",
        "item": {"type": "command_execution", "id": "i3", "command": "ls -la"},
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "run_item_stream_event"
    item = evs[0]["item"]
    assert item["type"] == "tool_call_item"
    assert item["tool_call_id"] == "i3"
    assert item["tool_name"] == "Bash"
    assert item["arguments"] == {"command": "ls -la"}


def test_item_completed_command_execution_emits_tool_call_output():
    """Codex emits output under ``aggregated_output`` (stdout+stderr
    combined), NOT ``output`` as the public docs suggest. Pinned from
    the 2026-06-01 raw-event log capture."""
    evs = _t({
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "id": "i3",
            "command": "ls",
            "aggregated_output": "a\nb\n",
            "exit_code": 0,
            "status": "completed",
        },
    })
    assert len(evs) == 1
    item = evs[0]["item"]
    assert item["type"] == "tool_call_output_item"
    assert item["tool_call_id"] == "i3"
    assert item["output"] == "a\nb\n"


def test_item_completed_command_execution_falls_back_to_output_key():
    """Forward-compat: if a future Codex version emits the field as
    ``output`` instead of ``aggregated_output``, we still pick it up."""
    evs = _t({
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "id": "i3",
            "command": "ls",
            "output": "fallback content",
            "exit_code": 0,
            "status": "completed",
        },
    })
    assert evs[0]["item"]["output"] == "fallback content"


def test_item_completed_command_execution_surfaces_nonzero_exit_code():
    """When a command fails with non-zero exit, surface the exit code
    so the agent doesn't blindly retry the same failing command."""
    evs = _t({
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "id": "i4",
            "command": "false",
            "aggregated_output": "",
            "exit_code": 1,
            "status": "failed",
        },
    })
    assert "[exit code: 1]" in evs[0]["item"]["output"]


def test_item_completed_command_execution_with_output_and_nonzero_exit_keeps_both():
    """Real Codex example: command produces output AND exits non-zero
    (e.g. ``lark-cli doctor --offline`` fails with diagnostic JSON)."""
    evs = _t({
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "id": "i5",
            "command": "lark-cli doctor --offline",
            "aggregated_output": '{"ok": false, "checks": [...]}',
            "exit_code": 1,
            "status": "failed",
        },
    })
    output = evs[0]["item"]["output"]
    assert "{" in output  # original content kept
    assert "[exit code: 1]" in output  # exit code appended


def test_item_started_mcp_tool_call_uses_server_tool_name():
    """Real codex CLI 0.135 emits ``type='mcp_tool_call'`` (not
    ``mcp_call`` as the public docs suggest). Pinned from the
    2026-06-02 raw-event log capture against slack_module.slack_status."""
    evs = _t({
        "type": "item.started",
        "item": {
            "type": "mcp_tool_call",
            "id": "m1",
            "server": "lark_module",
            "tool": "lark_cli",
            "arguments": {"command": "im +messages-send"},
        },
    })
    item = evs[0]["item"]
    assert item["tool_name"] == "mcp__lark_module__lark_cli"
    assert item["arguments"] == {"command": "im +messages-send"}


def test_item_completed_mcp_tool_call_jsonifies_dict_result():
    evs = _t({
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "id": "m1",
            "server": "lark_module",
            "tool": "lark_cli",
            "result": {"success": True, "ts": "1.0"},
        },
    })
    item = evs[0]["item"]
    assert item["type"] == "tool_call_output_item"
    # Result becomes JSON string
    assert "success" in item["output"]
    assert "true" in item["output"].lower()


def test_item_started_web_search_uses_websearch_name():
    evs = _t({
        "type": "item.started",
        "item": {"type": "web_search", "id": "w1", "query": "narranexus"},
    })
    item = evs[0]["item"]
    assert item["tool_name"] == "WebSearch"
    assert item["arguments"] == {"query": "narranexus"}


# ---------------- Turn lifecycle / usage ---------------------------


def test_turn_completed_emits_result_with_usage():
    evs = _t({
        "type": "turn.completed",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_input_tokens": 25,
        },
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "raw_response_event"
    assert evs[0]["data"]["type"] == "response.done"
    usage = evs[0]["data"]["usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["cached_input_tokens"] == 25


def test_turn_completed_missing_usage_defaults_to_zero():
    evs = _t({"type": "turn.completed"})
    assert evs[0]["data"]["usage"]["input_tokens"] == 0
    assert evs[0]["data"]["usage"]["output_tokens"] == 0


def test_turn_failed_emits_error():
    evs = _t({"type": "turn.failed", "error": "rate_limited"})
    assert len(evs) == 1
    assert evs[0]["data"]["type"] == "response.error"
    assert evs[0]["data"]["error_message"] == "rate_limited"
    assert evs[0]["data"]["error_type"] == "turn.failed"


def test_top_level_error_emits_error():
    evs = _t({"type": "error", "message": "connection refused"})
    assert evs[0]["data"]["type"] == "response.error"
    assert "connection refused" in evs[0]["data"]["error_message"]


def test_codex_text_event_is_consumed_by_response_processor():
    ev = _t({
        "type": "item.completed",
        "item": {"type": "agent_message", "id": "i1", "text": "hello world"},
    })[0]

    processor = ResponseProcessor()
    state = ExecutionState()
    results = list(processor.process(ev, state))

    assert len(results) == 1
    assert results[0].type == ResponseType.TEXT_DELTA
    assert results[0].message.delta == "hello world"
