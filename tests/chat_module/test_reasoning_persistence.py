"""
@file_name: test_reasoning_persistence.py
@author: Bin Liang
@date: 2026-04-23
@description: Persist Agent's reasoning (`final_output`) alongside the
user-visible reply so next-turn-LLM sees both.

Context (2026-04-23 production incident with agent_7f357515e25a): the
Agent kept looping on Lark incremental-scope authorization because the
`device_code` returned by `auth login --no-wait` was only in that turn's
tool_call_output_item — stripped from the next turn's context. The Agent
could neither paste the value into the follow-up `--device-code` call
nor recognize it had minted one already. Root cause: ChatModule stored
only the `send_message_to_user_directly.content` (what the user sees);
the Agent's own reasoning text (which could have carried the
`device_code` across turns) was discarded.

Fix:
  1. Storage side — hook_after_event_execution puts `final_output` on
     `meta_data.reasoning` when saving assistant messages (truncated to
     cap DB size).
  2. Load side — hook_data_gathering splices the stored reasoning into
     the assistant message `content` with `<my_reasoning>` /
     `<reply_to_user>` markers so the next turn's LLM reads both.

Tests here pin both sides.
"""
from __future__ import annotations

from typing import List

import pytest

from xyz_agent_context.module.chat_module.chat_module import ChatModule
from xyz_agent_context.schema import (
    ContextData,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_module(db_client):
    return ChatModule(
        agent_id="a_reasoning",
        user_id="u_reasoning",
        database_client=db_client,
        instance_id="chat_reasoning_instance",
    )


def _reply_progress(text: str) -> ProgressMessage:
    """A ProgressMessage wrapping a successful send_message_to_user_directly
    tool call — matches what AgentRuntime sends to the hook."""
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


def _hook_params(
    *,
    agent_loop_response: List,
    final_output: str = "",
    input_content: str = "show me my docs",
    event_id: str = "evt_reasoning_1",
) -> HookAfterExecutionParams:
    ctx = HookExecutionContext(
        event_id=event_id,
        agent_id="a_reasoning",
        user_id="u_reasoning",
        working_source=WorkingSource.CHAT,
    )
    io = HookIOData(input_content=input_content, final_output=final_output)
    trace = HookExecutionTrace(event_log=[], agent_loop_response=agent_loop_response)
    ctx_data = ContextData(
        agent_id="a_reasoning",
        user_id="u_reasoning",
        input_content=input_content,
    )
    return HookAfterExecutionParams(
        execution_ctx=ctx,
        io_data=io,
        trace=trace,
        ctx_data=ctx_data,
    )


# ---------------------------------------------------------------------------
# Storage side — hook_after_event_execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_after_event_persists_final_output_to_meta_reasoning(
    chat_module,
):
    """The assistant message stored by ChatModule must carry the Agent's
    reasoning on `meta_data.reasoning` so the next turn's load path can
    surface it to the LLM."""
    reasoning_text = (
        "Minted device_code=OaEmm_C8Jy40 for scope search:docs:read, "
        "URL sent to user. Next turn when user confirms, poll with "
        "auth login --device-code OaEmm_C8Jy40."
    )
    reply = _reply_progress("请点击以下链接完成授权: https://...")
    params = _hook_params(
        agent_loop_response=[reply],
        final_output=reasoning_text,
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_reasoning_instance"
    )
    messages = memory.get("messages", []) if memory else []

    assistants = [m for m in messages if m.get("role") == "assistant"]
    assert len(assistants) == 1, (
        f"expected one assistant message, got {len(assistants)}"
    )
    assert assistants[0]["meta_data"].get("reasoning") == reasoning_text, (
        "Assistant message must expose the Agent's reasoning on "
        "`meta_data.reasoning` — otherwise the next turn's load-side "
        "splice has nothing to work with."
    )


@pytest.mark.asyncio
async def test_long_reasoning_is_preserved_full_on_persist(chat_module):
    """Reasoning is stored as-is, no truncation. The Agent authored the
    text itself, so it's already self-limited; cutting it risks cutting
    exactly the long opaque value (device_code, file token, etc.) the
    Agent was trying to carry to the next turn."""
    long_reasoning = "A" * 5000
    reply = _reply_progress("reply body")
    params = _hook_params(
        agent_loop_response=[reply],
        final_output=long_reasoning,
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_reasoning_instance"
    )
    assistants = [m for m in memory.get("messages", []) if m.get("role") == "assistant"]
    stored = assistants[0]["meta_data"].get("reasoning", "")

    assert stored == long_reasoning, (
        "reasoning must be persisted verbatim; truncation would risk "
        "cutting the value the Agent wanted to carry across turns."
    )


@pytest.mark.asyncio
async def test_empty_final_output_does_not_pollute_meta(chat_module):
    """When `final_output` is empty (rare, usually means Agent only tool-
    called with no narration), meta_data.reasoning should either be
    absent or empty — not None / not the string 'None' / not a weird
    truncation marker."""
    reply = _reply_progress("reply body")
    params = _hook_params(
        agent_loop_response=[reply],
        final_output="",
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_reasoning_instance"
    )
    assistants = [m for m in memory.get("messages", []) if m.get("role") == "assistant"]
    meta = assistants[0].get("meta_data", {})
    reasoning = meta.get("reasoning", "")
    # Either key missing OR value is empty string — both acceptable.
    assert reasoning in ("", None), (
        f"empty reasoning must not leak placeholder text; got {reasoning!r}"
    )


# ---------------------------------------------------------------------------
# Load side — hook_data_gathering splicing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_data_gathering_splices_reasoning_into_assistant_content(
    chat_module,
):
    """When loading chat history for next turn's context, assistant
    messages with `meta_data.reasoning` must have their content wrapped
    so the LLM reads both the reasoning and the reply-to-user."""
    reasoning = "Minted device_code=OaEmm_C8Jy40 for scope search:docs:read."
    reply_to_user = "请点击 https://... 完成授权"

    await chat_module.event_memory_module.add_instance_json_format_memory(
        "ChatModule",
        "chat_reasoning_instance",
        {
            "messages": [
                {
                    "role": "user",
                    "content": "show me my docs",
                    "meta_data": {
                        "event_id": "evt_prev",
                        "timestamp": "2026-04-23T10:00:00",
                        "instance_id": "chat_reasoning_instance",
                        "working_source": "chat",
                    },
                },
                {
                    "role": "assistant",
                    "content": reply_to_user,
                    "meta_data": {
                        "event_id": "evt_prev",
                        "timestamp": "2026-04-23T10:00:05",
                        "instance_id": "chat_reasoning_instance",
                        "working_source": "chat",
                        "reasoning": reasoning,
                    },
                },
            ]
        },
    )

    ctx_data = ContextData(
        agent_id="a_reasoning",
        user_id="u_reasoning",
        input_content="done, try again",
    )
    chat_module.instance_ids = ["chat_reasoning_instance"]
    result = await chat_module.hook_data_gathering(ctx_data)

    history = result.chat_history or []
    assistants = [m for m in history if m.get("role") == "assistant"]
    assert len(assistants) == 1
    enriched = assistants[0]["content"]

    assert reasoning in enriched, (
        "reasoning from meta_data must appear in the LLM-facing content"
    )
    assert reply_to_user in enriched, (
        "original reply-to-user must be preserved in the content"
    )
    # Marker tags so the LLM knows which segment is which.
    assert "<my_reasoning>" in enriched and "</my_reasoning>" in enriched
    assert "<reply_to_user>" in enriched and "</reply_to_user>" in enriched


@pytest.mark.asyncio
async def test_hook_data_gathering_does_not_touch_user_messages(chat_module):
    """User messages do not have reasoning; their content must be
    untouched by the splicing pass."""
    await chat_module.event_memory_module.add_instance_json_format_memory(
        "ChatModule",
        "chat_reasoning_instance",
        {
            "messages": [
                {
                    "role": "user",
                    "content": "raw user question",
                    "meta_data": {
                        "event_id": "evt_prev",
                        "timestamp": "2026-04-23T10:00:00",
                        "instance_id": "chat_reasoning_instance",
                        "working_source": "chat",
                    },
                }
            ]
        },
    )

    ctx_data = ContextData(
        agent_id="a_reasoning",
        user_id="u_reasoning",
        input_content="next input",
    )
    chat_module.instance_ids = ["chat_reasoning_instance"]
    result = await chat_module.hook_data_gathering(ctx_data)

    users = [m for m in (result.chat_history or []) if m.get("role") == "user"]
    assert len(users) == 1
    assert users[0]["content"] == "raw user question", (
        "splicing must only affect assistant rows"
    )


@pytest.mark.asyncio
async def test_hook_data_gathering_leaves_assistant_without_reasoning_unchanged(
    chat_module,
):
    """Legacy assistant rows saved before this feature shipped have no
    `meta_data.reasoning`. Loader must leave their content unchanged
    (no empty `<my_reasoning>` block, no mangled tags)."""
    await chat_module.event_memory_module.add_instance_json_format_memory(
        "ChatModule",
        "chat_reasoning_instance",
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": "legacy reply text",
                    "meta_data": {
                        "event_id": "evt_old",
                        "timestamp": "2026-04-22T10:00:00",
                        "instance_id": "chat_reasoning_instance",
                        "working_source": "chat",
                        # no "reasoning" key
                    },
                }
            ]
        },
    )

    ctx_data = ContextData(
        agent_id="a_reasoning",
        user_id="u_reasoning",
        input_content="next",
    )
    chat_module.instance_ids = ["chat_reasoning_instance"]
    result = await chat_module.hook_data_gathering(ctx_data)

    assistants = [m for m in (result.chat_history or []) if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] == "legacy reply text"
    assert "<my_reasoning>" not in assistants[0]["content"]
