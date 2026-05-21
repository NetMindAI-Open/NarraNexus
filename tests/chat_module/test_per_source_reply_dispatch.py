"""
@file_name: test_per_source_reply_dispatch.py
@author: Bin Liang
@date: 2026-05-11
@description: Integration tests for MessageSource dispatch in
ChatModule.hook_after_event_execution.

Validates that for each WorkingSource value, the right reply tool is
recognised (so the row gets written as a real chat message rather than
a lossy `(Agent decided no response needed)` activity placeholder).

Pre-fix: only send_message_to_user_directly was recognised → Lark turns
where the agent really replied via `lark_cli +messages-send` ended up as
`message_type=activity` with content rewritten to "Handled a message
from X +N" — actual reply text lost at write time, the root cause of
the P0 #2 amnesia.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest

# Import the lark_module so it registers its handler. We rely on
# import-time registration here, mirroring how production backends boot.
import xyz_agent_context.module.lark_module  # noqa: F401
import xyz_agent_context.message_bus  # noqa: F401

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
        agent_id="a_disp",
        user_id="u_disp",
        database_client=db_client,
        instance_id="chat_disp_instance",
    )


def _progress_send_message(content: str) -> ProgressMessage:
    """Standard send_message_to_user_directly tool call. Recognised by
    the default and Lark handlers alike."""
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="send_message_to_user_directly",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": content},
        },
    )


def _progress_lark_cli_send(markdown_text: str) -> ProgressMessage:
    """lark_cli `im +messages-send` tool call. Recognised only when
    the Lark handler is registered."""
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="lark_cli +messages-send",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__lark_module__lark_cli",
            "arguments": {
                "command": f'im +messages-send --chat-id oc_test --markdown "{markdown_text}"',
            },
        },
    )


def _hook_params(
    *,
    working_source: WorkingSource,
    agent_loop_response: List,
    input_content: str = "hi",
    channel_tag: dict | None = None,
) -> HookAfterExecutionParams:
    ctx = HookExecutionContext(
        event_id="evt_disp_1",
        agent_id="a_disp",
        user_id="u_disp",
        working_source=working_source,
    )
    io = HookIOData(input_content=input_content, final_output="")
    trace = HookExecutionTrace(event_log=[], agent_loop_response=agent_loop_response)
    extra = {"channel_tag": channel_tag} if channel_tag else {}
    ctx_data = ContextData(
        agent_id="a_disp",
        user_id="u_disp",
        input_content=input_content,
        extra_data=extra,
    )
    event_stub = SimpleNamespace(
        created_at=datetime.now(timezone.utc),
    )
    return HookAfterExecutionParams(
        execution_ctx=ctx,
        io_data=io,
        trace=trace,
        ctx_data=ctx_data,
        event=event_stub,
    )


# --------- chat trigger: default handler, send_message_to_user_directly --------


@pytest.mark.asyncio
async def test_chat_trigger_send_message_recognised_as_reply(chat_module):
    """Baseline: chat trigger + send_message_to_user_directly → real
    chat row written, no activity tag."""
    reply = _progress_send_message("hello from chat trigger")
    params = _hook_params(
        working_source=WorkingSource.CHAT,
        agent_loop_response=[reply],
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_disp_instance"
    )
    messages = memory.get("messages", [])
    assistant_rows = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["content"] == "hello from chat trigger"
    assert assistant_rows[0]["meta_data"].get("message_type") != "activity"


# --------- lark trigger: lark handler recognises lark_cli reply tool ---------


@pytest.mark.asyncio
async def test_lark_trigger_lark_cli_send_recognised_as_reply(chat_module):
    """The fix: Lark turn where agent replies via `lark_cli +messages-send`
    must be written as a real chat row, NOT an activity row. The agent's
    actual reply text must be preserved.

    Pre-fix, this row would have been written as
    message_type=activity with content="Handled a message from u_disp..."
    — total information loss."""
    reply = _progress_lark_cli_send("你好啊 Loki")
    params = _hook_params(
        working_source=WorkingSource.LARK,
        agent_loop_response=[reply],
        channel_tag={
            "channel": "lark",
            "sender_name": "Loki",
            "sender_id": "ou_loki_test",
            "room_id": "oc_test",
            "room_name": "顺风耳, Loki, 阿良",
        },
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_disp_instance"
    )
    messages = memory.get("messages", [])
    assistant_rows = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_rows) == 1, f"expected exactly 1 assistant row, got {messages!r}"
    # Reply text from the --markdown flag must end up as the row's content.
    assert "你好啊" in assistant_rows[0]["content"], (
        f"lark_cli reply lost; row content={assistant_rows[0]['content']!r}"
    )
    # Crucially must NOT be flagged activity.
    assert assistant_rows[0]["meta_data"].get("message_type") != "activity"


@pytest.mark.asyncio
async def test_lark_trigger_non_send_lark_cli_does_not_count_as_reply(chat_module):
    """Defensive: an agent that runs `lark_cli +messages-list` (a non-
    reply lark_cli command) should NOT be treated as having replied —
    that turn should still go to the activity branch."""
    list_call = ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="lark_cli list",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__lark_module__lark_cli",
            "arguments": {"command": "im +messages-list --chat-id oc_test"},
        },
    )
    params = _hook_params(
        working_source=WorkingSource.LARK,
        agent_loop_response=[list_call],
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_disp_instance"
    )
    messages = memory.get("messages", [])
    assistant_rows = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    # No reply was sent → background-activity branch should fire.
    assert assistant_rows[0]["meta_data"].get("message_type") == "activity"


# --------- message_bus trigger: uses default reply tool ------------------


@pytest.mark.asyncio
async def test_message_bus_trigger_send_message_recognised(chat_module):
    """message_bus trigger registered to use send_message_to_user_directly
    (the trigger prompt explicitly instructs agents to call it for Owner
    Relay). Verify a real reply is preserved, not flagged activity."""
    reply = _progress_send_message("relay back from bus turn")
    params = _hook_params(
        working_source=WorkingSource.MESSAGE_BUS,
        agent_loop_response=[reply],
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_disp_instance"
    )
    messages = memory.get("messages", [])
    assistant_rows = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["content"] == "relay back from bus turn"
    assert assistant_rows[0]["meta_data"].get("message_type") != "activity"


@pytest.mark.asyncio
async def test_message_bus_trigger_no_reply_writes_activity(chat_module):
    """Bus turn where the agent really didn't reply → activity row.
    This is the SHOULD-be-activity case, not the bug class."""
    # No ProgressMessage at all → no reply tool match → activity.
    params = _hook_params(
        working_source=WorkingSource.MESSAGE_BUS,
        agent_loop_response=[],
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_disp_instance"
    )
    messages = memory.get("messages", [])
    assistant_rows = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["meta_data"].get("message_type") == "activity"


# --------- job trigger: default handler, no special tool registered -----


@pytest.mark.asyncio
async def test_job_trigger_send_message_recognised(chat_module):
    """Job trigger likewise uses send_message_to_user_directly (no
    handler registered, default fallback). When a scheduled job decides
    to message the user, the reply must be persisted."""
    reply = _progress_send_message("job finished, here's the result")
    params = _hook_params(
        working_source=WorkingSource.JOB,
        agent_loop_response=[reply],
    )

    await chat_module.hook_persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_disp_instance"
    )
    messages = memory.get("messages", [])
    assistant_rows = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["content"] == "job finished, here's the result"


# --------- regression --------------------------------------------------


@pytest.mark.asyncio
async def test_filtered_activity_row_invisible_to_long_term(chat_module):
    """End-to-end: write one chat row + one activity row, then load.
    long_term must drop the activity row."""
    # First turn: real chat reply.
    await chat_module.hook_persist_turn(_hook_params(
        working_source=WorkingSource.CHAT,
        agent_loop_response=[_progress_send_message("real chat")],
        input_content="user msg 1",
    ))
    # Second turn: lark trigger, no reply → activity row.
    await chat_module.hook_persist_turn(_hook_params(
        working_source=WorkingSource.LARK,
        agent_loop_response=[],  # no reply tool
        input_content="lark trigger payload",
    ))

    # Now hook_data_gathering should drop the activity row.
    from xyz_agent_context.schema import ContextData
    ctx_data = ContextData(
        agent_id="a_disp",
        user_id="u_disp",
        input_content="next turn",
    )
    ctx_data = await chat_module.hook_data_gathering(ctx_data)
    history = ctx_data.chat_history or []
    activities = [m for m in history
                  if (m.get("meta_data") or {}).get("message_type") == "activity"]
    assert activities == [], f"activity rows leaked into chat_history: {activities!r}"
