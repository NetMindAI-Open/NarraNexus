"""
@file_name: test_persist_fallback_after_error.py
@author: Bin Liang
@date: 2026-05-25
@description: Pins chat_module persistence when step_3 recovered a
fatal agent_loop crash via helper_llm.

Before 2026-05-25: a fatal ErrorMessage in `agent_loop_response`
collapsed the turn into a user-only row tagged `status=failed`, even
when step_3's fallback had already produced a real user-facing reply.
The next turn's `_apply_failed_turn_filter` then rewrote that user
message to "do NOT retry" — effectively erasing the recovered turn
from the agent's memory.

After 2026-05-25: persistence checks whether a synthetic
`send_message_to_user_directly` (carrying `reply_via=helper_llm_*`)
is present in the response stream. If so:
  - normal user+assistant pair is persisted;
  - assistant `meta_data.reply_via` carries the helper_llm tag;
  - assistant `meta_data.recovered_from_error` carries the
    original error details (for observability / future debug).
The user row is NOT tagged `status=failed`, so the next turn sees a
clean conversation history and `_apply_failed_turn_filter` does not
rewrite the user message.

When no synthetic reply exists (fallback genuinely failed too), the
old failed-user-only row behaviour is preserved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest

from xyz_agent_context.module.chat_module.chat_module import (
    ChatModule,
    _apply_failed_turn_filter,
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
        agent_id="a_recov",
        user_id="u_recov",
        database_client=db_client,
        instance_id="chat_recov_instance",
    )


def _hook_params(
    *,
    agent_loop_response: List,
    working_source: WorkingSource = WorkingSource.CHAT,
    input_content: str = "help me debug this",
) -> HookAfterExecutionParams:
    ctx = HookExecutionContext(
        event_id="evt_recov_1",
        agent_id="a_recov",
        user_id="u_recov",
        working_source=working_source,
    )
    io = HookIOData(input_content=input_content, final_output="")
    trace = HookExecutionTrace(event_log=[], agent_loop_response=list(agent_loop_response))
    ctx_data = ContextData(
        agent_id="a_recov",
        user_id="u_recov",
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


def _fatal_error(msg: str = "Claude CLI timed out") -> ErrorMessage:
    return ErrorMessage(
        error_message=msg,
        error_type="TimeoutError",
        severity="recovered",  # what step_3 actually emits post-recovery
    )


def _synthetic_recovered_reply(
    content: str = "I started looking but ran into a snag — here's what I found...",
) -> ProgressMessage:
    return ProgressMessage(
        step="3.4.fallback",
        title="Reply (helper_llm after_error)",
        description="Recovered reply.",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": content},
            "reply_via": "helper_llm_after_error",
        },
    )


# -------- happy path: fatal + recovered reply persists as normal turn -----


@pytest.mark.asyncio
async def test_fatal_with_recovered_reply_persists_as_normal_turn(chat_module):
    """When the fallback produced a user-facing reply, the turn is
    persisted as a normal user + assistant pair. No `status=failed`
    tag, no user-only row."""
    # In production the fatal ErrorMessage that step_3 yields after a
    # successful recovery has severity='recovered'. The legacy
    # severity='fatal' frames are the genuine no-recovery case (see
    # later test).
    params = _hook_params(agent_loop_response=[
        _fatal_error(),
        _synthetic_recovered_reply("Found A and B; couldn't fetch C."),
    ])

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_recov_instance"
    )
    messages = memory.get("messages", [])
    # Normal pair, not failed user-only.
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    # User meta has NO failed status.
    assert messages[0]["meta_data"].get("status") != "failed"
    assert "error_type" not in messages[0]["meta_data"]
    # Assistant content IS the recovered reply text.
    assert messages[1]["content"] == "Found A and B; couldn't fetch C."
    # Assistant meta records the recovery for observability.
    assert messages[1]["meta_data"].get("reply_via") == "helper_llm_after_error"


@pytest.mark.asyncio
async def test_recovered_turn_does_not_trigger_failed_turn_annotation(chat_module):
    """The next turn's prompt-assembly path runs `_apply_failed_turn_filter`
    over the history. A recovered turn must pass through unchanged — we
    don't want the agent to see a 'do NOT retry' annotation for a turn
    where the user already got a usable reply."""
    params = _hook_params(agent_loop_response=[
        _fatal_error(),
        _synthetic_recovered_reply("partial findings + suggestion"),
    ])
    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_recov_instance"
    )
    messages = memory.get("messages", [])
    filtered = _apply_failed_turn_filter(messages)

    # Filter is a no-op on a recovered turn.
    assert len(filtered) == 2
    assert filtered[0]["content"] == "help me debug this"
    assert "do NOT retry" not in filtered[0]["content"]
    # Assistant message survives (the failed-turn filter drops failed
    # assistant rows; a recovered one must pass through).
    assert filtered[1]["role"] == "assistant"


# -------- preserved behaviour: fatal + NO recovered reply ---------------


@pytest.mark.asyncio
async def test_fatal_without_recovered_reply_keeps_failed_user_only_row(
    chat_module,
):
    """If step_3's fallback ALSO failed (helper_llm errored / cancelled
    / produced empty), there is no synthetic send_message. We must
    keep the historical failed-user-only behaviour — better an honest
    'this turn died' than a fabricated reply."""
    legacy_fatal = ErrorMessage(
        error_message="boom",
        error_type="RuntimeError",
        severity="fatal",
    )
    params = _hook_params(agent_loop_response=[legacy_fatal])

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_recov_instance"
    )
    messages = memory.get("messages", [])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["meta_data"]["status"] == "failed"
    assert messages[0]["meta_data"]["error_type"] == "RuntimeError"


# -------- recovered_after_reply: agent spoke, then fatal hit ------------


@pytest.mark.asyncio
async def test_organic_reply_then_recovered_after_reply_persists_organic(
    chat_module,
):
    """Agent called send_message_to_user_directly organically, then a
    follow-up step crashed (severity=recovered_after_reply). The
    persisted reply is the ORGANIC content, not a fallback. Meta has
    no reply_via=helper_llm_* tag — this was a real reply, not a
    recovery."""
    organic_reply = ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="send_message",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": "Here's the answer."},
        },
    )
    post_reply_error = ErrorMessage(
        error_message="follow-up step blew up",
        error_type="RuntimeError",
        severity="recovered_after_reply",
    )
    params = _hook_params(agent_loop_response=[organic_reply, post_reply_error])

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_recov_instance"
    )
    messages = memory.get("messages", [])
    assert len(messages) == 2
    assert messages[1]["content"] == "Here's the answer."
    # Not a helper_llm reply — no fallback tag.
    assert "helper_llm_" not in (messages[1]["meta_data"].get("reply_via") or "")
    # User row is NOT tagged failed.
    assert messages[0]["meta_data"].get("status") != "failed"
