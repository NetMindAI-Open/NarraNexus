"""
@file_name: test_bootstrap_greeting_order.py
@author: Bin Liang
@date: 2026-05-11
@description: Bug fix — bootstrap greeting must precede the user's first
query in the persisted timeline.

Both the chat-history API (`backend/routes/agents_chat_history.py`) and
the frontend timeline (`frontend/src/components/chat/ChatPanel.tsx`)
sort messages by `meta_data.timestamp` ascending. Before this fix,
ChatModule wrote the bootstrap greeting with `utc_now()` (hook-end)
while the user's first message used `event.created_at` (turn-start).
Because the agent loop takes seconds to minutes, greeting timestamp >
user timestamp, and the greeting rendered AFTER the user query bubble
— the P0 "agent主动问好的消息跑到query底下了" from the bug tracker.

Fix: anchor the greeting timestamp strictly before the user message
(`event.created_at - 1ms`, or `utc_now() - 1ms` as defensive fallback)
so persisted order matches what the user expects to see.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import List

import pytest

from xyz_agent_context.bootstrap.template import BOOTSTRAP_GREETING
from xyz_agent_context.module.chat_module.chat_module import ChatModule
from xyz_agent_context.utils import utc_now
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


# -------- fixtures ------------------------------------------------------


@pytest.fixture
def chat_module(db_client):
    return ChatModule(
        agent_id="a_boot",
        user_id="u_boot",
        database_client=db_client,
        instance_id="chat_boot_instance",
    )


def _success_progress_with_reply(text: str) -> ProgressMessage:
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
    event_created_at: datetime,
    input_content: str = "Hi! I'd like to call you Echo, and I'm Alice.",
    bootstrap_active: bool = True,
) -> HookAfterExecutionParams:
    ctx = HookExecutionContext(
        event_id="evt_boot_1",
        agent_id="a_boot",
        user_id="u_boot",
        working_source=WorkingSource.CHAT,
    )
    io = HookIOData(input_content=input_content, final_output="")
    trace = HookExecutionTrace(event_log=[], agent_loop_response=agent_loop_response)
    ctx_data = ContextData(
        agent_id="a_boot",
        user_id="u_boot",
        input_content=input_content,
        bootstrap_active=bootstrap_active,
    )
    # SimpleNamespace stands in for Event — chat_module only reads
    # `event.created_at`, so a dataclass-shaped stub is enough and avoids
    # constructing a full Event (which requires many narrative fields).
    event_stub = SimpleNamespace(created_at=event_created_at)
    return HookAfterExecutionParams(
        execution_ctx=ctx,
        io_data=io,
        trace=trace,
        ctx_data=ctx_data,
        event=event_stub,
    )


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


# -------- bug fix · greeting must precede user message ------------------


@pytest.mark.asyncio
async def test_bootstrap_greeting_timestamp_precedes_user_message(chat_module):
    """Persisted order must be greeting → user → assistant, with
    timestamps strictly increasing on the greeting→user boundary so
    timestamp-ascending sorts (frontend + history API) render
    greeting on top of the timeline.

    Simulates the production timing: the agent loop takes ~30s, so by
    the time `hook_after_event_execution` runs, `utc_now()` is ~30s
    past `event.created_at`. Pre-fix, greeting used `utc_now()` and
    therefore landed after the user message."""
    event_started_at = utc_now() - timedelta(seconds=30)
    reply = _success_progress_with_reply("Nice to meet you, Alice. Echo it is.")
    params = _hook_params(
        agent_loop_response=[reply],
        event_created_at=event_started_at,
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_boot_instance"
    )
    messages = memory.get("messages", []) if memory else []

    assert len(messages) == 3, f"expected greeting+user+assistant, got {messages!r}"
    greeting, user_msg, assistant_msg = messages

    assert greeting["role"] == "assistant"
    assert greeting["content"] == BOOTSTRAP_GREETING
    assert greeting["meta_data"].get("bootstrap") is True
    assert user_msg["role"] == "user"
    assert assistant_msg["role"] == "assistant"

    greeting_ts = _parse(greeting["meta_data"]["timestamp"])
    user_ts = _parse(user_msg["meta_data"]["timestamp"])
    assert greeting_ts < user_ts, (
        f"greeting must precede user message; "
        f"greeting_ts={greeting_ts} user_ts={user_ts}"
    )


@pytest.mark.asyncio
async def test_bootstrap_greeting_precedes_user_even_when_event_missing(chat_module):
    """Defensive: if `params.event` is None, the hook still falls back
    sensibly and greeting_ts is no later than user_ts."""
    reply = _success_progress_with_reply("Hello!")
    params = _hook_params(
        agent_loop_response=[reply],
        event_created_at=utc_now() - timedelta(seconds=30),
    )
    params.event = None

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_boot_instance"
    )
    messages = memory.get("messages", [])
    assert len(messages) == 3
    greeting, user_msg, _ = messages
    greeting_ts = _parse(greeting["meta_data"]["timestamp"])
    user_ts = _parse(user_msg["meta_data"]["timestamp"])
    assert greeting_ts <= user_ts


# -------- regression · no bootstrap when inactive -----------------------


@pytest.mark.asyncio
async def test_no_bootstrap_when_inactive(chat_module):
    """When `bootstrap_active=False`, no greeting row is prepended."""
    reply = _success_progress_with_reply("Sure.")
    params = _hook_params(
        agent_loop_response=[reply],
        event_created_at=utc_now() - timedelta(seconds=30),
        bootstrap_active=False,
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_boot_instance"
    )
    messages = memory.get("messages", [])
    assert len(messages) == 2
    assert all(m["meta_data"].get("bootstrap") is not True for m in messages)
