"""
@file_name: test_error_severity_and_fallback.py
@author: Bin Liang
@date: 2026-05-11
@description: Pins three P0 #3 fixes:

A) Failed-turn rows persist BOTH error_type AND error_message, and the
   next-turn annotation surfaces the detail. Pre-fix: only error_type
   survived; ops had to grep stderr to learn why a turn failed.

B) Recoverable ErrorMessage no longer kills the whole turn. Only
   severity="fatal" entries trip _detect_fatal_error_in_agent_loop.

C) final_output fallback: when the agent produced LLM-native output but
   didn't call a registered reply tool, we persist `io_data.final_output`
   as the assistant content (with `reply_via=final_output_fallback`) so
   the next turn doesn't see "(Agent decided no response needed)" — the
   self-reinforcing failure loop that caused much of Xiong's "agent
   decided no response" baseline noise.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest

from xyz_agent_context.module.chat_module.chat_module import (
    ChatModule,
    _FAILED_TURN_ANNOTATION_TEMPLATE,
    _apply_failed_turn_filter,
    _detect_fatal_error_in_agent_loop,
)
from xyz_agent_context.schema import (
    ContextData,
    ErrorMessage,
    HookAfterExecutionParams,
    ProgressMessage,
    ProgressStatus,
)
from xyz_agent_context.schema.hook_schema import (
    HookExecutionContext,
    HookExecutionTrace,
    HookIOData,
    WorkingSource,
)


# -------- fixtures ------------------------------------------------------


@pytest.fixture
def chat_module(db_client):
    return ChatModule(
        agent_id="a_sev",
        user_id="u_sev",
        database_client=db_client,
        instance_id="chat_sev_instance",
    )


def _hook_params(
    *,
    working_source: WorkingSource = WorkingSource.CHAT,
    agent_loop_response: List = (),
    input_content: str = "what's the weather?",
    final_output: str = "",
) -> HookAfterExecutionParams:
    ctx = HookExecutionContext(
        event_id="evt_sev_1",
        agent_id="a_sev",
        user_id="u_sev",
        working_source=working_source,
    )
    io = HookIOData(input_content=input_content, final_output=final_output)
    trace = HookExecutionTrace(event_log=[], agent_loop_response=list(agent_loop_response))
    ctx_data = ContextData(
        agent_id="a_sev",
        user_id="u_sev",
        input_content=input_content,
    )
    event_stub = SimpleNamespace(created_at=datetime.now(timezone.utc))
    return HookAfterExecutionParams(
        execution_ctx=ctx,
        io_data=io,
        trace=trace,
        ctx_data=ctx_data,
        event=event_stub,
    )


# -------- severity gating ----------------------------------------------


def test_recoverable_error_not_treated_as_fatal():
    """The whole point of severity: a transient rate-limit signal mid-
    loop should NOT collapse the turn into a failed row."""
    err = ErrorMessage(
        error_message="rate limit hit, retrying upstream",
        error_type="rate_limit",
        severity="recoverable",
    )
    assert _detect_fatal_error_in_agent_loop([err]) is None


def test_fatal_error_is_detected():
    err = ErrorMessage(
        error_message="Claude Code CLI did not respond for 90s",
        error_type="TimeoutError",
        severity="fatal",
    )
    sig = _detect_fatal_error_in_agent_loop([err])
    assert sig is not None
    assert sig["error_type"] == "TimeoutError"
    assert "90s" in sig["error_message"]


def test_error_message_default_severity_is_fatal():
    """Backwards-compat: a bare ErrorMessage(error_message=..., error_type=...)
    without explicit severity must still be treated as fatal so legacy
    test fixtures and any not-yet-updated emitter keep working."""
    err = ErrorMessage(error_message="boom", error_type="api_error")
    assert _detect_fatal_error_in_agent_loop([err]) is not None


# -------- failed-turn detail persistence -------------------------------


@pytest.mark.asyncio
async def test_failed_turn_persists_error_message_in_meta(chat_module):
    err = ErrorMessage(
        error_message="Anthropic returned 502 Bad Gateway after 3 retries",
        error_type="UpstreamUnavailable",
        severity="fatal",
    )
    params = _hook_params(agent_loop_response=[err])

    await chat_module.hook_after_event_execution(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_sev_instance"
    )
    messages = memory.get("messages", [])
    assert len(messages) == 1
    user_meta = messages[0]["meta_data"]
    assert user_meta["status"] == "failed"
    assert user_meta["error_type"] == "UpstreamUnavailable"
    # NEW: full message preserved alongside type.
    assert "502 Bad Gateway" in user_meta["error_message"]


def test_failed_turn_annotation_includes_error_message():
    """When the next turn's _apply_failed_turn_filter rewrites a failed
    user row, the annotation must surface both error_type and
    error_message so the LLM (and ops) sees why the previous turn died."""
    failed_row = {
        "role": "user",
        "content": "deploy the staging branch please",
        "meta_data": {
            "status": "failed",
            "error_type": "UpstreamUnavailable",
            "error_message": "Anthropic returned 502 after 3 retries",
        },
    }
    out = _apply_failed_turn_filter([failed_row])
    assert len(out) == 1
    annotated = out[0]
    assert "UpstreamUnavailable" in annotated["content"]
    assert "502" in annotated["content"]
    assert "do NOT retry" in annotated["content"].lower() or \
           "do not retry" in annotated["content"].lower()


def test_failed_turn_annotation_handles_missing_error_message_field():
    """Legacy rows pre-dating this fix only carry error_type. The
    template must format without crashing and show a clear placeholder."""
    legacy_row = {
        "role": "user",
        "content": "legacy question",
        "meta_data": {
            "status": "failed",
            "error_type": "rate_limit",
            # no error_message field
        },
    }
    out = _apply_failed_turn_filter([legacy_row])
    assert "rate_limit" in out[0]["content"]
    # Doesn't crash with KeyError. The placeholder text appears.
    assert "no detail captured" in out[0]["content"]


# -------- final_output fallback (Bug B) --------------------------------


def _no_reply_progress() -> ProgressMessage:
    """A progress message that is NOT send_message_to_user_directly —
    something else the agent ran during the turn."""
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="get_chat_history",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__get_chat_history",
            "arguments": {"instance_id": "chat_sev_instance"},
        },
    )


@pytest.mark.asyncio
async def test_helper_llm_fallback_marker_is_propagated(chat_module):
    """When step_3_agent_loop's helper_llm fallback fires, it emits a
    synthetic send_message_to_user_directly ProgressMessage carrying
    `details.reply_via = "helper_llm_fallback"`. chat_module persists
    the resulting assistant row with that same marker so observability
    tooling can separate organic replies from recovered ones.

    The fallback logic itself lives in step_3 now (see
    test_step_3_agent_loop_helper_llm_fallback.py); this test pins
    chat_module's downstream handling of the marker."""
    synthetic_fallback = ProgressMessage(
        step="3.4.fallback",
        title="Reply (helper_llm fallback)",
        description="Agent did not call send_message; helper_llm generated reply.",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": "Recovered reply text."},
            "reply_via": "helper_llm_fallback",
        },
    )
    params = _hook_params(
        agent_loop_response=[synthetic_fallback],
        final_output="agent internal reasoning that should NOT leak as reply",
    )

    await chat_module.hook_after_event_execution(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_sev_instance"
    )
    messages = memory.get("messages", [])
    assistant_rows = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    row = assistant_rows[0]
    # Content is the synthetic tool call's arguments.content (NOT the
    # agent's raw final_output, which is reasoning).
    assert row["content"] == "Recovered reply text."
    assert "internal reasoning" not in row["content"]
    # Marker propagated.
    assert row["meta_data"].get("reply_via") == "helper_llm_fallback"


@pytest.mark.asyncio
async def test_fallback_does_not_fire_when_send_message_was_called(chat_module):
    """Regression: when the agent DID call send_message_to_user_directly,
    we must keep using its content, not blindly switch to final_output."""
    send_msg = ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="send_message_to_user_directly",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": "tool-call content (preferred)"},
        },
    )
    params = _hook_params(
        agent_loop_response=[send_msg],
        final_output="final_output would be a side-channel summary",
    )

    await chat_module.hook_after_event_execution(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_sev_instance"
    )
    assistant_rows = [m for m in memory.get("messages", []) if m["role"] == "assistant"]
    assert assistant_rows[0]["content"] == "tool-call content (preferred)"
    assert assistant_rows[0]["meta_data"].get("reply_via") != "final_output_fallback"


@pytest.mark.asyncio
async def test_no_reply_tool_and_no_helper_llm_fallback_persists_placeholder(chat_module):
    """When neither the reply tool nor the synthetic helper_llm fallback
    ProgressMessage are present, chat_module honestly records that the
    turn had no user-facing reply. The [NO-REPLY] warning log fires so
    ops can audit it. This is the upstream-helper-llm-also-failed
    case — see step_3_agent_loop for the recovery path."""
    params = _hook_params(
        agent_loop_response=[_no_reply_progress()],
        # final_output is irrelevant now — chat_module no longer copies
        # it into the assistant row (that was the violation of the
        # thinking-vs-speaking design that 2026-05-12 removed).
        final_output="some internal reasoning that must NOT be persisted as reply",
    )

    await chat_module.hook_after_event_execution(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_sev_instance"
    )
    assistant_rows = [m for m in memory.get("messages", []) if m["role"] == "assistant"]
    assert assistant_rows[0]["content"] == "(Agent decided no response needed)"
    # final_output / reasoning must NOT leak into content.
    assert "internal reasoning" not in assistant_rows[0]["content"]
    # No fallback marker — there was no fallback.
    assert assistant_rows[0]["meta_data"].get("reply_via") is None


# -------- regression: template format string --------------------------


def test_failed_turn_annotation_template_has_required_fields():
    """The template must accept original / error_type / error_message
    — guard against accidental signature changes."""
    rendered = _FAILED_TURN_ANNOTATION_TEMPLATE.format(
        original="what's the weather?",
        error_type="TimeoutError",
        error_message="CLI did not respond for 90s",
    )
    assert "what's the weather?" in rendered
    assert "TimeoutError" in rendered
    assert "CLI did not respond" in rendered
