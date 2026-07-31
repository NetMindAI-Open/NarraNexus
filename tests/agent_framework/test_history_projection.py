"""
@file_name: test_history_projection.py
@author: Bin Liang
@date: 2026-07-29
@description: fold_event_log_to_messages — invariants of native turn replay.

The one non-negotiable: the folded sequence must be PROVIDER-LEGAL.
Every assistant ``tool_calls`` entry is followed by a tool message for
each call before the next assistant message; orphan outputs never
produce a tool message; malformed rows degrade the replay, never raise.
"""
from __future__ import annotations

import json

from xyz_agent_context.agent_framework.loop.history_projection import (
    fold_event_log_to_messages,
)


def _thinking(monologue: str | None, content: str = "coalesced") -> dict:
    step: dict = {"type": "thinking", "content": content}
    if monologue is not None:
        step["monologue"] = monologue
    return step


def _call(call_id: str, name: str = "bash", args: dict | None = None) -> dict:
    return {
        "type": "tool_call",
        "tool_name": name,
        "tool_call_id": call_id,
        "arguments": args if args is not None else {"command": "ls"},
    }


def _output(call_id: str | None, output: str = "ok") -> dict:
    step: dict = {"type": "tool_output", "output": output}
    if call_id is not None:
        step["tool_call_id"] = call_id
    return step


def test_single_step_turn_folds_to_assistant_plus_tool():
    messages = fold_event_log_to_messages(
        [_thinking("I will list files. "), _call("c1"), _output("c1", "file.txt")]
    )
    assert [m["role"] for m in messages] == ["assistant", "tool"]
    assistant = messages[0]
    assert assistant["content"] == "I will list files. "
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "command": "ls"
    }
    assert messages[1] == {"role": "tool", "tool_call_id": "c1", "content": "file.txt"}


def test_parallel_calls_pair_by_id_in_completion_order():
    messages = fold_event_log_to_messages(
        [
            _call("a"),
            _call("b"),
            _output("b", "b-first"),
            _output("a", "a-second"),
        ]
    )
    assert [m["role"] for m in messages] == ["assistant", "tool", "tool"]
    assert [c["id"] for c in messages[0]["tool_calls"]] == ["a", "b"]
    assert messages[1]["tool_call_id"] == "b"
    assert messages[2]["tool_call_id"] == "a"


def test_multi_step_turn_keeps_alternation():
    messages = fold_event_log_to_messages(
        [
            _thinking("step one. "),
            _call("c1"),
            _output("c1"),
            _thinking("step two. "),
            _call("c2"),
            _output("c2"),
        ]
    )
    assert [m["role"] for m in messages] == ["assistant", "tool", "assistant", "tool"]
    assert messages[2]["content"] == "step two. "


def test_dangling_call_is_closed_before_next_assistant_message():
    messages = fold_event_log_to_messages(
        [
            _call("dangling"),
            _output("dangling_other", "orphan, dropped"),  # flushes batch 1
            _thinking("moving on. "),
            _call("c2"),
            _output("c2"),
        ]
    )
    roles = [m["role"] for m in messages]
    # assistant(dangling)'s call must be answered synthetically BEFORE
    # the next assistant message, or the request is provider-illegal.
    assert roles == ["assistant", "tool", "assistant", "tool"]
    assert messages[1]["tool_call_id"] == "dangling"
    assert "no result" in messages[1]["content"]
    assert messages[3]["tool_call_id"] == "c2"


def test_calls_without_intervening_output_merge_into_one_batch():
    """No output between two calls = one assistant message with both
    calls (the parallel-call shape); the unanswered one closes at the
    batch boundary, keeping the sequence provider-legal."""
    messages = fold_event_log_to_messages(
        [_call("dangling"), _thinking("moving on. "), _call("c2"), _output("c2")]
    )
    assert [m["role"] for m in messages] == ["assistant", "tool", "tool"]
    assert [c["id"] for c in messages[0]["tool_calls"]] == ["dangling", "c2"]
    answered = {m["tool_call_id"] for m in messages[1:]}
    assert answered == {"dangling", "c2"}


def test_dangling_call_at_end_is_closed():
    messages = fold_event_log_to_messages([_call("c1")])
    assert [m["role"] for m in messages] == ["assistant", "tool"]
    assert messages[1]["tool_call_id"] == "c1"


def test_orphan_output_is_dropped():
    assert fold_event_log_to_messages([_output("ghost")]) == []


def test_output_without_id_answers_oldest_open_call():
    messages = fold_event_log_to_messages(
        [_call("a"), _call("b"), _output(None, "for-a"), _output(None, "for-b")]
    )
    assert messages[1]["tool_call_id"] == "a"
    assert messages[2]["tool_call_id"] == "b"


def test_cot_without_monologue_yields_nothing():
    """Claude/codex logs (thinking rows carry no monologue, text never
    enters all_steps): a text-only turn folds to [] and the caller keeps
    the flattened row."""
    assert fold_event_log_to_messages([_thinking(None), _thinking(None)]) == []


def test_event_log_entry_wrapper_shape_is_unwrapped():
    entries = [
        {"timestamp": "2026-07-29T00:00:00Z", "type": "tool_call", "content": _call("c1")},
        {"timestamp": "2026-07-29T00:00:01Z", "type": "tool_output", "content": _output("c1")},
    ]
    messages = fold_event_log_to_messages(entries)
    assert [m["role"] for m in messages] == ["assistant", "tool"]


def test_malformed_rows_never_raise():
    messages = fold_event_log_to_messages(
        [
            None,
            42,
            {"no_type": True},
            {"type": "tool_call"},  # missing name/id
            {"type": "tool_call", "tool_name": "x", "tool_call_id": "ok",
             "arguments": {"f": object()}},  # unserializable args
            _output("ok", "still pairs"),
            {"type": "agent_final_output", "content": "dup"},
        ]
    )
    assert [m["role"] for m in messages] == ["assistant", "tool"]
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_empty_and_none_logs():
    assert fold_event_log_to_messages([]) == []
    assert fold_event_log_to_messages(None) == []  # type: ignore[arg-type]
