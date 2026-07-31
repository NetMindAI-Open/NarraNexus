"""
@file_name: test_output_transfer_pending.py
@date: 2026-07-30
@description: claude_code / codex name-first tool events are best-effort.

Streaming SDK paths hand over partial_json increments, so the name can
arrive before the arguments; non-streaming paths hand over both at once.
So we only pin "when only the name is known, the item is pending" — not
that a pending item is always produced. That depends on whether the
provider streams, and the platform does not paper over the difference
(binding rule #15).
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.loop.output_transfer import (
    tool_call_item,
)


def test_tool_call_item_marks_pending_when_arguments_absent():
    item = tool_call_item(tool_call_id="c1", tool_name="Bash", arguments=None)
    assert item["pending"] is True
    assert item["arguments"] == {}
    assert item["tool_call_id"] == "c1"
    assert item["tool_name"] == "Bash"


def test_tool_call_item_is_complete_when_arguments_present():
    item = tool_call_item(
        tool_call_id="c1", tool_name="Bash", arguments={"command": "ls"},
    )
    assert item["pending"] is False
    assert item["arguments"] == {"command": "ls"}
