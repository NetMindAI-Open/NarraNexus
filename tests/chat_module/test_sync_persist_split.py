"""
@file_name: test_sync_persist_split.py
@author: Bin Liang
@date: 2026-05-20
@description: Short-reply amnesia fix — conversation persistence split.

The conversation row a fast follow-up turn reads is now written SYNCHRONOUSLY in
ChatModule.hook_persist_turn (in-request, before the WS closes / before the
background hooks fire), while the heavy Part-B embedding stays in the backgrounded
hook_after_event_execution. Previously the WRITE itself lived in the background
hook, which could lag seconds-to-tens-of-seconds; a user replying the instant they
saw the answer raced that write and the next turn read history missing the
exchange ("amnesia"). These tests pin the split:
- hook_persist_turn WRITES the conversation row.
- hook_after_event_execution does NOT add a conversation row (it only embeds the
  already-written pair, located by event_id).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

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


@pytest.fixture
def chat_module(db_client):
    return ChatModule(
        agent_id="a_split",
        user_id="u_split",
        database_client=db_client,
        instance_id="chat_split_instance",
    )


def _reply(text: str) -> ProgressMessage:
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


def _params(event_id: str = "evt_split_1", input_content: str = "hi") -> HookAfterExecutionParams:
    return HookAfterExecutionParams(
        execution_ctx=HookExecutionContext(
            event_id=event_id,
            agent_id="a_split",
            user_id="u_split",
            working_source=WorkingSource.CHAT,
        ),
        io_data=HookIOData(input_content=input_content, final_output=""),
        trace=HookExecutionTrace(
            event_log=[], agent_loop_response=[_reply("hello there")]
        ),
        ctx_data=ContextData(
            agent_id="a_split", user_id="u_split", input_content=input_content
        ),
    )


async def _load(chat_module) -> list:
    mem = await chat_module.event_memory_module.search_instance_json_format_memory(
        chat_module.config.name, chat_module.instance_id
    )
    return (mem or {}).get("messages", [])


async def test_persist_turn_writes_conversation_synchronously(chat_module):
    await chat_module.hook_persist_turn(_params())
    messages = await _load(chat_module)
    roles = [m["role"] for m in messages]
    assert "user" in roles and "assistant" in roles
    assert any(
        m["role"] == "assistant" and "hello there" in m["content"] for m in messages
    )


async def test_after_event_execution_does_not_write_conversation(chat_module):
    # The write is hook_persist_turn's job; the background hook must ONLY embed.
    params = _params()
    await chat_module.hook_persist_turn(params)
    before = await _load(chat_module)

    chat_module._embed_message_pair = AsyncMock()
    await chat_module.hook_after_event_execution(params)

    after = await _load(chat_module)
    assert len(after) == len(before)  # no extra conversation rows
    chat_module._embed_message_pair.assert_awaited_once()
    _, kwargs = chat_module._embed_message_pair.call_args
    assert kwargs["event_id"] == "evt_split_1"
    assert "hello there" in kwargs["assistant_content"]
