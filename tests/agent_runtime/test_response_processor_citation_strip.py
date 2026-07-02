"""
@file_name: test_response_processor_citation_strip.py
@date: 2026-06-08
@description: Live-streaming citation-strip path inside ResponseProcessor.

When the model calls ``send_message_to_user_directly`` (or any other
user-reply tool) with ``content`` that contains OpenAI Responses-API
``citeturnNviewN`` markers, the live-streamed ProgressMessage shipped
to the frontend must carry CLEAN content — not the raw model output.

There are two strip sites:
  1. ``MessageSourceHandler.extract_reply_text`` — persist + IM
     forward paths (DB writes, channel-specific reply rendering).
  2. ``ResponseProcessor._handle_run_item_stream_event`` (THIS path)
     — live WS streaming to the chat UI.

This file pins behaviour (2). Without the strip here, the live
chat bubble shows raw ``citeturn6view0`` tokens glued to sentence
ends while the persisted DB row is clean — visible inconsistency.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import (
    ResponseProcessor,
    ResponseType,
)
from xyz_agent_context.schema import ProgressMessage


def _reply_tool_event(tool_name: str, content: str) -> dict:
    return {
        "type": "run_item_stream_event",
        "item": {
            "type": "tool_call_item",
            "tool_name": tool_name,
            "tool_call_id": "call_1",
            "arguments": {"content": content},
        },
    }


def test_send_message_content_stripped_in_progress_message():
    rp = ResponseProcessor()
    state = ExecutionState()
    raw = "习近平于6月8日抵达平壤。citeturn12view0"

    events = list(rp._handle_run_item_stream_event(
        _reply_tool_event(
            "mcp__chat_module__send_message_to_user_directly", raw
        ),
        state,
    ))

    assert len(events) == 1
    progress = events[0].message
    assert isinstance(progress, ProgressMessage)
    cleaned_content = progress.details["arguments"]["content"]
    assert "citeturn" not in cleaned_content
    assert cleaned_content == "习近平于6月8日抵达平壤。"


def test_multiple_citation_tokens_stripped_in_progress_message():
    rp = ResponseProcessor()
    state = ExecutionState()
    raw = (
        "1. 高考开考citeturn12view1\n"
        "2. 中朝外交citeturn12view0\n"
        "3. 菲律宾地震citeturn13view0 citeturn12view2"
    )

    events = list(rp._handle_run_item_stream_event(
        _reply_tool_event(
            "mcp__chat_module__send_message_to_user_directly", raw
        ),
        state,
    ))

    cleaned = events[0].message.details["arguments"]["content"]
    assert "citeturn" not in cleaned
    # Each line cleanly stripped — content order preserved.
    assert "1. 高考开考" in cleaned
    assert "2. 中朝外交" in cleaned
    assert "3. 菲律宾地震" in cleaned


def test_non_reply_tool_content_left_unmodified():
    """Other tools (Bash, web_search, custom MCP) keep their args
    verbatim — the strip is reply-text-only. Imagine a hypothetical
    citation-generator tool whose ``content`` param legitimately
    contains the string ``cite...something...``; we must not mangle
    its input."""
    rp = ResponseProcessor()
    state = ExecutionState()
    raw_content_with_cite_lookalike = "build citetest1tool0 from input"

    events = list(rp._handle_run_item_stream_event(
        {
            "type": "run_item_stream_event",
            "item": {
                "type": "tool_call_item",
                "tool_name": "Bash",
                "tool_call_id": "call_x",
                "arguments": {"content": raw_content_with_cite_lookalike},
            },
        },
        state,
    ))

    # Bash is NOT a reply tool, so even a citation-shaped string
    # passes through untouched.
    assert events[0].message.details["arguments"]["content"] == raw_content_with_cite_lookalike


def test_reply_tool_without_content_field_safe():
    """Defensive: a malformed reply-tool call with no ``content``
    arg should not crash the strip path."""
    rp = ResponseProcessor()
    state = ExecutionState()

    events = list(rp._handle_run_item_stream_event(
        {
            "type": "run_item_stream_event",
            "item": {
                "type": "tool_call_item",
                "tool_name": "mcp__chat_module__send_message_to_user_directly",
                "tool_call_id": "call_x",
                "arguments": {},
            },
        },
        state,
    ))

    assert len(events) == 1
    # No exception; arguments dict survives.
    assert events[0].message.details["arguments"] == {}


def test_lark_cli_markdown_field_stripped():
    """Lark CLI puts the user-visible text in ``markdown`` (not
    ``content``); the strip covers that field too. Same path applies
    for Slack and Telegram CLI wrappers."""
    rp = ResponseProcessor()
    state = ExecutionState()
    raw_markdown = "Lark message body citeturn5view3"

    events = list(rp._handle_run_item_stream_event(
        {
            "type": "run_item_stream_event",
            "item": {
                "type": "tool_call_item",
                "tool_name": "lark_cli +messages-send",
                "tool_call_id": "call_x",
                "arguments": {"markdown": raw_markdown},
            },
        },
        state,
    ))

    cleaned = events[0].message.details["arguments"]["markdown"]
    assert cleaned == "Lark message body"
