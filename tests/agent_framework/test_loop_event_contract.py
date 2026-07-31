"""
@file_name: test_loop_event_contract.py
@date: 2026-07-27
@description: Contract tests for agent_framework.loop.events — the single
source of truth for the legacy driver event-dict shapes.

The six shapes (raw_response_event × {text.delta, done, error} and
run_item_stream_event × {thinking_item, tool_call_item,
tool_call_output_item}) are consumed by ResponseProcessor, the frontend
tool-pairing logic, the billing chain (response.done usage) and the
helper-LLM fallback. These tests pin the exact string values so that any
producer (output_transfer, claude sdk synthesizers, a future nexus_power
LegacyEventAdapter) and any consumer reference one shared definition.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.loop.events import (
    CLI_ERROR_TYPES,
    DATA_TYPE_DONE,
    DATA_TYPE_ERROR,
    DATA_TYPE_TEXT_DELTA,
    ITEM_TYPE_MESSAGE_OUTPUT,
    ITEM_TYPE_THINKING,
    ITEM_TYPE_TOOL_CALL,
    ITEM_TYPE_TOOL_CALL_OUTPUT,
    TYPE_RAW_RESPONSE_EVENT,
    TYPE_RUN_ITEM_STREAM_EVENT,
    USAGE_CACHE_READ_KEYS,
    raw_error_event,
    raw_text_delta_event,
)
from xyz_agent_context.agent_framework.loop.output_transfer import output_transfer


# ---------------- The exact legacy strings are pinned ----------------
# These values are load-bearing wire contract: ResponseProcessor branches
# on them, the remote executor streams them verbatim, and the frontend
# replay tables classify by the derived kinds. Changing any value is a
# breaking protocol change, never a refactor.


def test_top_level_type_values_are_the_legacy_strings():
    assert TYPE_RAW_RESPONSE_EVENT == "raw_response_event"
    assert TYPE_RUN_ITEM_STREAM_EVENT == "run_item_stream_event"


def test_data_type_values_are_the_legacy_strings():
    assert DATA_TYPE_TEXT_DELTA == "response.text.delta"
    assert DATA_TYPE_DONE == "response.done"
    assert DATA_TYPE_ERROR == "response.error"


def test_item_type_values_are_the_legacy_strings():
    assert ITEM_TYPE_THINKING == "thinking_item"
    assert ITEM_TYPE_TOOL_CALL == "tool_call_item"
    assert ITEM_TYPE_TOOL_CALL_OUTPUT == "tool_call_output_item"
    assert ITEM_TYPE_MESSAGE_OUTPUT == "message_output_item"


def test_cli_error_type_vocabulary_matches_sdk():
    # The claude-agent-sdk error taxonomy that response_processor's
    # severity triage and the circuit breaker rely on.
    assert CLI_ERROR_TYPES == frozenset(
        {
            "authentication_failed",
            "billing_error",
            "rate_limit",
            "invalid_request",
            "server_error",
            "unknown",
        }
    )


def test_usage_cache_read_dual_vocabulary():
    # Anthropic spells it cache_read_input_tokens; OpenAI/codex spell it
    # cached_input_tokens. accumulate_usage checks both, in this order.
    assert USAGE_CACHE_READ_KEYS == ("cache_read_input_tokens", "cached_input_tokens")


# ---------------- Constructor helpers produce legacy shapes ----------


def test_raw_text_delta_event_shape():
    assert raw_text_delta_event("hi") == {
        "type": "raw_response_event",
        "data": {"type": "response.text.delta", "delta": "hi"},
    }


def test_raw_error_event_shape():
    assert raw_error_event("boom", "server_error") == {
        "type": "raw_response_event",
        "data": {
            "type": "response.error",
            "error_message": "boom",
            "error_type": "server_error",
        },
    }


# ---------------- Producers emit only declared types -----------------


def test_output_transfer_codex_events_use_declared_types():
    """Behavioral pin: a representative codex stream translates into
    events whose type strings are exactly the declared constants."""
    tool_call = output_transfer(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": "c1",
                "command": "ls",
                "status": "completed",
                "aggregated_output": "ok",
                "exit_code": 0,
            },
        },
        transfer_type="codex_cli",
    )
    done = output_transfer(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 5},
        },
        transfer_type="codex_cli",
    )
    emitted = tool_call + done
    assert emitted, "fixture events must translate to at least one event"
    for ev in emitted:
        assert ev["type"] in {TYPE_RAW_RESPONSE_EVENT, TYPE_RUN_ITEM_STREAM_EVENT}
        if ev["type"] == TYPE_RAW_RESPONSE_EVENT:
            assert ev["data"]["type"] in {
                DATA_TYPE_TEXT_DELTA,
                DATA_TYPE_DONE,
                DATA_TYPE_ERROR,
            }
        else:
            assert ev["item"]["type"] in {
                ITEM_TYPE_THINKING,
                ITEM_TYPE_TOOL_CALL,
                ITEM_TYPE_TOOL_CALL_OUTPUT,
            }
