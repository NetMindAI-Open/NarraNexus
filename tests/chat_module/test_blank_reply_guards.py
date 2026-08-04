"""
@file_name: test_blank_reply_guards.py
@author: Bin Liang
@date: 2026-08-04
@description: Whitespace-only replies must never persist as assistant rows.

The 2026-07-13 blank-bubble report: a reply that is *only* whitespace
("\n" after citation-token stripping, or literal spaces) is truthy, so
it slipped past the falsy-only guard in hook_persist_turn and landed in
history as a blank message bubble. The silent-batch branch already
strips (`if not content.strip()`); the main path must match, falling
back to the same placeholder an empty reply gets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
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

AGENT = "a_blank"
USER = "u_blank"
INSTANCE = "chat_blank_instance"


@pytest.fixture
def chat_module(db_client):
    return ChatModule(
        agent_id=AGENT,
        user_id=USER,
        database_client=db_client,
        instance_id=INSTANCE,
    )


def _params(
    *,
    agent_loop_response: List | None = None,
    interrupted: bool = False,
) -> HookAfterExecutionParams:
    return HookAfterExecutionParams(
        execution_ctx=HookExecutionContext(
            event_id="evt_blank_1",
            agent_id=AGENT,
            user_id=USER,
            working_source=WorkingSource.CHAT,
        ),
        io_data=HookIOData(
            input_content="hello?",
            final_output="",
            interrupted=interrupted,
        ),
        trace=HookExecutionTrace(
            event_log=[], agent_loop_response=list(agent_loop_response or [])
        ),
        ctx_data=ContextData(
            agent_id=AGENT, user_id=USER, input_content="hello?"
        ),
        event=SimpleNamespace(created_at=datetime.now(timezone.utc)),
    )


def _reply(content: str) -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="send_message_to_user_directly",
        description="reply",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": content},
        },
    )


async def _rows(chat_module) -> list:
    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", INSTANCE
    )
    return memory.get("messages", [])


@pytest.mark.asyncio
async def test_newline_only_reply_persists_placeholder(chat_module):
    await chat_module.hook_persist_turn(
        _params(agent_loop_response=[_reply("\n")])
    )
    messages = await _rows(chat_module)
    assert messages[-1]["content"] == "(Agent decided no response needed)"


@pytest.mark.asyncio
async def test_spaces_only_reply_persists_placeholder(chat_module):
    await chat_module.hook_persist_turn(
        _params(agent_loop_response=[_reply("   ")])
    )
    messages = await _rows(chat_module)
    assert messages[-1]["content"] == "(Agent decided no response needed)"


@pytest.mark.asyncio
async def test_whitespace_reply_on_interrupted_turn_says_interrupted(chat_module):
    """The strip guard must not clobber the interrupt branch: a stopped
    turn whose only output was whitespace reads "cut short", not "chose
    not to answer"."""
    await chat_module.hook_persist_turn(
        _params(agent_loop_response=[_reply("\n")], interrupted=True)
    )
    messages = await _rows(chat_module)
    assert messages[-1]["content"] == "(Interrupted by user)"


@pytest.mark.asyncio
async def test_real_reply_with_surrounding_whitespace_is_kept(chat_module):
    """Guard is strip-for-emptiness only — real content keeps its exact
    persisted form."""
    await chat_module.hook_persist_turn(
        _params(agent_loop_response=[_reply("Here you go.\n")])
    )
    messages = await _rows(chat_module)
    assert "Here you go." in messages[-1]["content"]
