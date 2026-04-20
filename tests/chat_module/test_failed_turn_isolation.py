"""
@file_name: test_failed_turn_isolation.py
@author: Bin Liang
@date: 2026-04-20
@description: Bug 8 — a turn that errors out must NOT contaminate the next
turn's history.

Before this fix, ChatModule.hook_after_event_execution stored every
(user, assistant) pair regardless of whether the agent loop succeeded.
If the agent crashed mid-turn, the next turn's prompt showed the
user's failed question with an empty / partial / placeholder assistant
reply — and the LLM would dutifully try to "finish" the previous task
on top of whatever the user actually asked next.

The fix has two halves:

1. `step_3_agent_loop.py` appends the `ErrorMessage` to
   `agent_loop_response` (in addition to yielding it), so downstream
   hooks can see the failure signal.
2. `chat_module.py`:
   - On error, store only the user message, with
     ``meta_data.status="failed"`` and ``meta_data.error_type``.
   - When loading history for the prompt (long-term in
     `hook_data_gathering`, short-term in `_load_short_term_memory`),
     transform failed user messages into an annotated note that tells
     the LLM "this turn errored, do NOT retry".
"""
from __future__ import annotations

from typing import List

import pytest

from xyz_agent_context.module.chat_module.chat_module import ChatModule
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
    """ChatModule backed by the in-memory SQLite fixture."""
    return ChatModule(
        agent_id="a_bug8",
        user_id="u_bug8",
        database_client=db_client,
        instance_id="chat_bug8_instance",
    )


def _hook_params(
    *,
    agent_loop_response: List,
    input_content: str = "What's the weather in Paris?",
    final_output: str = "",
    working_source: WorkingSource = WorkingSource.CHAT,
) -> HookAfterExecutionParams:
    """Build a HookAfterExecutionParams that mirrors what AgentRuntime
    hands to ``hook_after_event_execution`` at end-of-turn."""
    ctx = HookExecutionContext(
        event_id="evt_bug8_1",
        agent_id="a_bug8",
        user_id="u_bug8",
        working_source=working_source,
    )
    io = HookIOData(input_content=input_content, final_output=final_output)
    trace = HookExecutionTrace(event_log=[], agent_loop_response=agent_loop_response)
    ctx_data = ContextData(
        agent_id="a_bug8",
        user_id="u_bug8",
        input_content=input_content,
    )
    return HookAfterExecutionParams(
        execution_ctx=ctx,
        io_data=io,
        trace=trace,
        ctx_data=ctx_data,
    )


def _success_progress_with_reply(text: str) -> ProgressMessage:
    """A ProgressMessage that wraps a successful `send_message_to_user_directly` tool call."""
    return ProgressMessage(
        step="3.2",
        title="Tool call",
        description="send_message_to_user_directly",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": text},
        },
    )


# -------- half 1 · ErrorMessage must reach the hook ---------------------


@pytest.mark.asyncio
async def test_failed_turn_stores_user_message_only_with_failed_status(
    chat_module,
):
    """When the agent loop errors, the hook should persist ONLY the user
    question with status=failed + error_type. No fake assistant pair."""
    error = ErrorMessage(
        error_message="Rate limit exceeded",
        error_type="rate_limit",
    )
    params = _hook_params(agent_loop_response=[error])

    await chat_module.hook_after_event_execution(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_bug8_instance"
    )
    messages = memory.get("messages", []) if memory else []

    assert len(messages) == 1, (
        f"expected exactly 1 stored message (user with failed status), "
        f"got {len(messages)}: {messages!r}"
    )
    msg = messages[0]
    assert msg["role"] == "user"
    assert msg["content"] == "What's the weather in Paris?"
    meta = msg.get("meta_data", {})
    assert meta.get("status") == "failed"
    assert meta.get("error_type") == "rate_limit"


@pytest.mark.asyncio
async def test_failed_turn_with_partial_assistant_output_still_not_stored_as_pair(
    chat_module,
):
    """Even if the agent managed a partial `send_message_to_user_directly`
    before crashing, the turn is still a failure — we must not store
    the partial reply as a completed assistant answer that would trick
    the next turn into continuation.

    The preserved artifact is the USER's question with status=failed.
    The partial assistant text is dropped (it was never a complete
    answer; keeping it as a normal pair is exactly the bug)."""
    partial_reply = _success_progress_with_reply("Looking that up...")
    error = ErrorMessage(
        error_message="Upstream 500",
        error_type="api_error",
    )
    params = _hook_params(agent_loop_response=[partial_reply, error])

    await chat_module.hook_after_event_execution(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_bug8_instance"
    )
    messages = memory.get("messages", []) if memory else []

    # Must be single user msg, not a pair.
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["meta_data"].get("status") == "failed"


# -------- half 1 regression · successful turn unchanged -----------------


@pytest.mark.asyncio
async def test_successful_turn_still_stores_user_and_assistant_pair(
    chat_module,
):
    """Regression: normal turn is unaffected."""
    reply = _success_progress_with_reply("It's 21°C and sunny in Paris.")
    params = _hook_params(agent_loop_response=[reply])

    await chat_module.hook_after_event_execution(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_bug8_instance"
    )
    messages = memory.get("messages", []) if memory else []

    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "It's 21°C and sunny in Paris."
    # Neither side should be tagged failed.
    assert messages[0]["meta_data"].get("status") != "failed"
    assert messages[1]["meta_data"].get("status") != "failed"


# -------- half 2 · load-side transformation -----------------------------


@pytest.mark.asyncio
async def test_long_term_memory_annotates_failed_user_turn(chat_module):
    """When the prompt is built, a failed user turn must be transformed
    into an annotated note (preserves the original wording but tells
    the LLM explicitly that it errored and must NOT be retried)."""
    # Simulate a failed turn persisted earlier, then a follow-up.
    await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_bug8_instance"
    )
    # Store directly via the memory module to avoid coupling to the
    # storage half of this fix (that half has its own tests above).
    await chat_module.event_memory_module.add_instance_json_format_memory(
        "ChatModule",
        "chat_bug8_instance",
        {
            "messages": [
                {
                    "role": "user",
                    "content": "What's the weather in Paris?",
                    "meta_data": {
                        "event_id": "evt_old",
                        "timestamp": "2026-04-20T10:00:00",
                        "instance_id": "chat_bug8_instance",
                        "working_source": "chat",
                        "status": "failed",
                        "error_type": "rate_limit",
                    },
                },
            ],
            "last_event_id": "evt_old",
            "updated_at": "2026-04-20T10:00:00",
        },
    )

    ctx_data = ContextData(
        agent_id="a_bug8",
        user_id="u_bug8",
        input_content="Tell me a joke",
    )
    ctx_data = await chat_module.hook_data_gathering(ctx_data)

    loaded = ctx_data.chat_history or []
    # Find the failed user turn in the loaded history.
    failed_turns = [
        m for m in loaded
        if m.get("meta_data", {}).get("status") == "failed"
    ]
    assert len(failed_turns) == 1, f"expected 1 failed turn, got {loaded!r}"

    turn = failed_turns[0]
    assert turn["role"] == "user"
    # Must include explicit language telling the LLM not to retry.
    combined = turn["content"].lower()
    assert "previous" in combined or "earlier" in combined
    assert "error" in combined or "fail" in combined
    assert "do not retry" in combined or "not retry" in combined or "do not repeat" in combined
    # Original wording must be preserved somewhere so context references
    # ("it", "that question") still resolve if needed.
    assert "weather in paris" in combined


def test_filter_drops_failed_assistant_rows_defensively():
    """Historical data (pre-fix) may already have stored failed turns as
    (user, assistant) pairs where assistant content is
    "(Agent decided no response needed)" or a partial. Whatever we call
    when preparing messages for the prompt must drop assistant rows
    tagged ``status=failed`` so they don't feed back into prompts.

    Unit-tests the filter directly rather than going through
    `_load_short_term_memory` (which hits the real DB singleton)."""
    from xyz_agent_context.module.chat_module.chat_module import (
        _apply_failed_turn_filter,
    )

    messages = [
        {
            "role": "user",
            "content": "ok",
            "meta_data": {"status": "ok"},
        },
        {
            "role": "assistant",
            "content": "(Agent decided no response needed)",
            "meta_data": {"status": "failed", "error_type": "api_error"},
        },
        {
            "role": "assistant",
            "content": "real answer",
            "meta_data": {"status": "ok"},
        },
    ]

    filtered = _apply_failed_turn_filter(messages)

    # Assistant-with-status=failed rows must not appear.
    assert not any(
        m.get("role") == "assistant"
        and m.get("meta_data", {}).get("status") == "failed"
        for m in filtered
    )
    # Non-failed messages must pass through untouched.
    kinds = [(m["role"], m["content"]) for m in filtered]
    assert ("user", "ok") in kinds
    assert ("assistant", "real answer") in kinds
