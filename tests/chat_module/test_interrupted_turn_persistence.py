"""
@file_name: test_interrupted_turn_persistence.py
@author: Bin Liang
@date: 2026-07-30
@description: Interrupted turns persist as real history, correctly marked.

Before interrupt continuity, a user stop skipped hook_persist_turn
entirely: neither the user's message nor any assistant row was written —
the next turn had no idea the exchange happened. Now the pair persists;
the assistant placeholder must read "cut short by the user", never
"chose not to answer" (they mean opposite things to the next turn's
model), and meta_data.interrupted marks the row for consumers.
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

AGENT = "a_intr"
USER = "u_intr"
INSTANCE = "chat_intr_instance"


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
    interrupted: bool,
    agent_loop_response: List | None = None,
    final_output: str = "",
) -> HookAfterExecutionParams:
    return HookAfterExecutionParams(
        execution_ctx=HookExecutionContext(
            event_id="evt_intr_1",
            agent_id=AGENT,
            user_id=USER,
            working_source=WorkingSource.CHAT,
        ),
        io_data=HookIOData(
            input_content="run the full analysis",
            final_output=final_output,
            interrupted=interrupted,
        ),
        trace=HookExecutionTrace(
            event_log=[], agent_loop_response=list(agent_loop_response or [])
        ),
        ctx_data=ContextData(
            agent_id=AGENT, user_id=USER, input_content="run the full analysis"
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
async def test_interrupted_turn_without_reply_persists_marked_pair(chat_module):
    await chat_module.hook_persist_turn(_params(interrupted=True))
    messages = await _rows(chat_module)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["meta_data"].get("status") != "failed"
    assert messages[1]["content"] == "(Interrupted by user)"
    assert messages[1]["meta_data"].get("interrupted") is True


@pytest.mark.asyncio
async def test_interrupted_after_reply_keeps_the_reply(chat_module):
    """The agent spoke, THEN the user stopped the follow-up work: the
    real reply persists, still marked interrupted."""
    await chat_module.hook_persist_turn(
        _params(
            interrupted=True,
            agent_loop_response=[_reply("Here is the first half of the analysis.")],
        )
    )
    messages = await _rows(chat_module)
    assert messages[-1]["content"] == "Here is the first half of the analysis."
    assert messages[-1]["meta_data"].get("interrupted") is True


@pytest.mark.asyncio
async def test_uninterrupted_no_reply_placeholder_unchanged(chat_module):
    await chat_module.hook_persist_turn(_params(interrupted=False))
    messages = await _rows(chat_module)
    assert messages[-1]["content"] == "(Agent decided no response needed)"
    assert "interrupted" not in messages[-1]["meta_data"]
