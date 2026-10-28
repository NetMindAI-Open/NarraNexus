"""
@file_name: test_bootstrap_greeting_order.py
@author: Bin Liang
@date: 2026-05-11
@description: Bug fix — bootstrap greeting must precede the user's first
query in the persisted timeline.

Both the chat-history API (`backend/routes/agents/chat_history.py`) and
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

from narranexus.platform.bootstrap.template import BOOTSTRAP_GREETING
from narranexus_plugins.chat_module.chat_module import ChatModule
from narranexus.platform.repository.event_memory_repository import EventMemoryRepository
from narranexus.platform.utils import utc_now
from narranexus.platform.schema import (
    ContextData,
    HookAfterExecutionParams,
    ProgressMessage,
    ProgressStatus,
)
from narranexus.platform.schema.hook_schema import (
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
        description="reply_owner",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__reply_owner",
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
    the time `after_turn` runs, `utc_now()` is ~30s
    past `event.created_at`. Pre-fix, greeting used `utc_now()` and
    therefore landed after the user message."""
    event_started_at = utc_now() - timedelta(seconds=30)
    reply = _success_progress_with_reply("Nice to meet you, Alice. Echo it is.")
    params = _hook_params(
        agent_loop_response=[reply],
        event_created_at=event_started_at,
    )

    await chat_module.persist_turn(params)

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

    await chat_module.persist_turn(params)

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
async def test_seeded_greeting_not_duplicated_by_hook_and_orders_first(chat_module):
    """Cross-path invariant for the step_1 provision-time seed
    (chat_module.seed_bootstrap_greeting → then persist_turn in the same
    turn): the real seed writes the greeting; the hook, seeing a non-empty
    history, must NOT prepend a second one; and the greeting (stamped at
    turn-start - 1ms by the writer) must sort before the user's first message.

    Deleting the hook's `len(messages)==0` guard, or letting the seed anchor the
    greeting at a mid-turn `now()`, turns this red."""
    from narranexus_plugins.chat_module import seed_bootstrap_greeting

    event_started_at = utc_now() - timedelta(seconds=30)

    # The REAL seed runs first (as step_1 does), anchored to this turn's start.
    # ids come from the fixture so the test doesn't rely on the memory table
    # being keyed by instance_id alone.
    wrote = await seed_bootstrap_greeting(
        chat_module.event_memory_module.db,
        chat_module.agent_id,
        chat_module.user_id,
        chat_module.instance_id,
        BOOTSTRAP_GREETING,
        event_started_at,
    )
    assert wrote is True

    reply = _success_progress_with_reply("Hi back, Alice.")
    params = _hook_params(
        agent_loop_response=[reply],
        event_created_at=event_started_at,
        bootstrap_active=True,
    )

    await chat_module.persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_boot_instance"
    )
    messages = memory.get("messages", [])

    bootstrap_rows = [m for m in messages if m["meta_data"].get("bootstrap") is True]
    assert len(bootstrap_rows) == 1, f"greeting duplicated: {messages!r}"
    # Order: seeded greeting first, then user, then assistant reply.
    assert messages[0]["meta_data"].get("bootstrap") is True
    user_msg = next(m for m in messages if m["role"] == "user")
    assert _parse(bootstrap_rows[0]["meta_data"]["timestamp"]) < _parse(
        user_msg["meta_data"]["timestamp"]
    )


@pytest.mark.asyncio
async def test_hook_greeting_carries_no_event_id(chat_module):
    """Shenzhen-r2 B2, identity half: the hook used to stamp the greeting with
    the CURRENT run's event_id — the greeting and the turn's real reply then
    shared the (role, event_id) identity the frontend timeline dedups on.
    The greeting belongs to no run; its row must carry no event_id (the
    step_1 seed already writes none — the two writers must agree)."""
    reply = _success_progress_with_reply("Hi back, Alice.")
    params = _hook_params(
        agent_loop_response=[reply],
        event_created_at=utc_now() - timedelta(seconds=30),
        bootstrap_active=True,
    )
    await chat_module.persist_turn(params)
    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_boot_instance"
    )
    greeting = next(
        m for m in memory.get("messages", [])
        if m["meta_data"].get("bootstrap") is True
    )
    assert "event_id" not in greeting["meta_data"], greeting


@pytest.mark.asyncio
async def test_hook_skips_greeting_when_a_sibling_instance_has_history(db_client):
    """Shenzhen-r2 B2, re-greet half: a new narrative creates a fresh EMPTY
    chat instance while bootstrap is still active — the hook's per-instance
    emptiness check alone re-greeted there, and the extra assistant row read
    as a second reply to whatever the user just asked. First contact is
    per-(agent, user): sibling history suppresses the prepend."""
    from tests.chat_module.test_chat_writes import _register_chat_instance

    agent, user = "a_boot2", "u_boot2"
    await _register_chat_instance(db_client, "chat_boot2_a", agent, user)
    await _register_chat_instance(db_client, "chat_boot2_b", agent, user)
    prior = EventMemoryRepository(agent, user, db_client)
    await prior.add_instance_json_format_memory(
        "ChatModule",
        "chat_boot2_a",
        {"messages": [
            {"role": "user", "content": "hi", "meta_data": {"timestamp": utc_now().isoformat()}},
            {"role": "assistant", "content": "hello", "meta_data": {"timestamp": utc_now().isoformat()}},
        ]},
    )

    module = ChatModule(
        agent_id=agent,
        user_id=user,
        database_client=db_client,
        instance_id="chat_boot2_b",
    )
    reply = _success_progress_with_reply("second narrative reply")
    params = _hook_params(
        agent_loop_response=[reply],
        event_created_at=utc_now() - timedelta(seconds=30),
        bootstrap_active=True,
    )
    # _hook_params hardcodes a_boot ids; repoint them at this module's pair.
    params.execution_ctx.agent_id = agent
    params.execution_ctx.user_id = user
    params.ctx_data.agent_id = agent
    params.ctx_data.user_id = user

    await module.persist_turn(params)

    memory = await module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_boot2_b"
    )
    messages = memory.get("messages", [])
    assert messages, "the turn itself must still persist"
    assert all(
        m["meta_data"].get("bootstrap") is not True for m in messages
    ), f"re-greeted a later narrative: {messages!r}"


@pytest.mark.asyncio
async def test_no_bootstrap_when_inactive(chat_module):
    """When `bootstrap_active=False`, no greeting row is prepended."""
    reply = _success_progress_with_reply("Sure.")
    params = _hook_params(
        agent_loop_response=[reply],
        event_created_at=utc_now() - timedelta(seconds=30),
        bootstrap_active=False,
    )

    await chat_module.persist_turn(params)

    memory = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", "chat_boot_instance"
    )
    messages = memory.get("messages", [])
    assert len(messages) == 2
    assert all(m["meta_data"].get("bootstrap") is not True for m in messages)
