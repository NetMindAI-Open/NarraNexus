"""
@file_name: test_narrative_routing_signal.py
@author: Bin Liang
@date: 2026-05-20
@description: step_4 detection of switch_narrative / create_narrative signals (Fix #2 P3).

The basic_info narrative tools are SIGNALS: the agent calls switch_narrative /
create_narrative, and step_4 detects the call in the agent-loop response (the
tool process and runtime are separate) and does the re-attribution.
_detect_narrative_routing_signal returns the LAST such (kind, args) or None.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_4_persist_results import (
    _detect_narrative_routing_signal,
)
from xyz_agent_context.schema import ProgressMessage, ProgressStatus


def _tool_pm(tool_name: str, arguments: dict) -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1", title="tool", description="", status=ProgressStatus.COMPLETED,
        details={"tool_name": tool_name, "arguments": arguments},
    )


def test_detects_switch_with_narrative_id():
    resp = [_tool_pm("mcp__basic_info_module__switch_narrative", {"narrative_id": "nar_x"})]
    assert _detect_narrative_routing_signal(resp) == ("switch", {"narrative_id": "nar_x"})


def test_detects_create_with_title():
    resp = [_tool_pm("mcp__basic_info_module__create_narrative", {"title": "New topic", "description": "d"})]
    kind, args = _detect_narrative_routing_signal(resp)
    assert kind == "create"
    assert args["title"] == "New topic"


def test_none_when_no_routing_tool():
    resp = [_tool_pm("mcp__chat_module__send_message_to_user_directly", {"content": "hi"})]
    assert _detect_narrative_routing_signal(resp) is None
    assert _detect_narrative_routing_signal([]) is None


def test_last_signal_wins():
    resp = [
        _tool_pm("mcp__basic_info_module__switch_narrative", {"narrative_id": "nar_a"}),
        _tool_pm("mcp__basic_info_module__create_narrative", {"title": "T"}),
    ]
    kind, _ = _detect_narrative_routing_signal(resp)
    assert kind == "create"
