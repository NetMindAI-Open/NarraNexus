"""
@file_name: test_silent_batch_persist.py
@author: NetMind.AI
@date: 2026-07-02
@description: ChatModule.hook_persist_turn — silent batch write path.

Contract: when `params.ctx_data.extra_data["batch_messages"]` is a non-empty
list, ChatModule writes ONE user row per batch entry into
`instance_json_format_memory`, preserving each entry's `sender_id`,
`sender_name`, `timestamp`, `event_id`, and (optional) `attachments`.
It does NOT append an assistant row — silent runs skip step_3, so there
is nothing agent-authored to persist.

Used by IM channels (Matrix / Lark / Slack) for group non-@ messages and
reconnect burst backfill. If this write path breaks, group memory
regresses to "only @-triggered messages retained" (Slack-style gap),
which defeats the point of silent mode.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.chat_module.chat_module import ChatModule
from xyz_agent_context.schema import (
    ContextData,
    HookAfterExecutionParams,
)
from xyz_agent_context.schema.hook_schema import (
    HookExecutionContext,
    HookExecutionTrace,
    HookIOData,
    WorkingSource,
)


AGENT_ID = "a_silent"
USER_ID = "u_silent"
INSTANCE_ID = "chat_silent_instance"


@pytest.fixture
def chat_module(db_client):
    return ChatModule(
        agent_id=AGENT_ID,
        user_id=USER_ID,
        database_client=db_client,
        instance_id=INSTANCE_ID,
    )


def _params_with_batch(batch, working_source=WorkingSource.NARRAMESSENGER):
    ctx_data = ContextData(
        agent_id=AGENT_ID,
        user_id=USER_ID,
        input_content="(merged batch input)",
        extra_data={
            "batch_messages": batch,
            "channel_tag": {"channel": "narramessenger_matrix", "room_id": "!room:h"},
        },
    )
    return HookAfterExecutionParams(
        execution_ctx=HookExecutionContext(
            event_id="evt_batch_root",
            agent_id=AGENT_ID,
            user_id=USER_ID,
            working_source=working_source,
        ),
        io_data=HookIOData(input_content="(merged batch input)", final_output=""),
        trace=HookExecutionTrace(event_log=[], agent_loop_response=[]),
        ctx_data=ctx_data,
    )


@pytest.mark.asyncio
async def test_silent_batch_writes_one_user_row_per_entry_no_assistant(chat_module):
    batch = [
        {
            "event_id": "$evt1",
            "timestamp": "2026-07-02T12:00:00+00:00",
            "sender_id": "@alice:h",
            "sender_name": "Alice",
            "content": "morning everyone",
        },
        {
            "event_id": "$evt2",
            "timestamp": "2026-07-02T12:00:05+00:00",
            "sender_id": "@bob:h",
            "sender_name": "Bob",
            "content": "hey Alice",
        },
        {
            "event_id": "$evt3",
            "timestamp": "2026-07-02T12:00:12+00:00",
            "sender_id": "@alice:h",
            "sender_name": "Alice",
            "content": "how's the deploy?",
        },
    ]
    await chat_module.hook_persist_turn(_params_with_batch(batch))

    stored = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", INSTANCE_ID
    )
    assert stored is not None, "batch write did not create the memory row"
    msgs = stored.get("messages", [])
    # Exactly N user rows, zero assistant rows.
    assert len(msgs) == 3, f"expected 3 rows, got {len(msgs)}: {msgs}"
    assert all(m["role"] == "user" for m in msgs), (
        f"silent batch must not emit assistant rows: {msgs}"
    )
    # Per-row identity preserved.
    assert msgs[0]["content"] == "morning everyone"
    assert msgs[0]["meta_data"]["sender_id"] == "@alice:h"
    assert msgs[0]["meta_data"]["sender_name"] == "Alice"
    assert msgs[0]["meta_data"]["event_id"] == "$evt1"
    assert msgs[0]["meta_data"]["timestamp"] == "2026-07-02T12:00:00+00:00"
    assert msgs[0]["meta_data"]["silent"] is True
    # Later rows should carry their own event_id, not root event_id.
    assert msgs[1]["meta_data"]["event_id"] == "$evt2"
    assert msgs[2]["meta_data"]["event_id"] == "$evt3"


@pytest.mark.asyncio
async def test_silent_batch_skips_empty_content_without_attachments(chat_module):
    """A batch entry with no content and no attachments is a no-op row —
    it must not create a phantom empty user message."""
    batch = [
        {"event_id": "$evt1", "sender_id": "@a:h", "content": "real message"},
        {"event_id": "$evt2", "sender_id": "@b:h", "content": "   "},  # whitespace only
        {"event_id": "$evt3", "sender_id": "@c:h", "content": ""},      # empty
    ]
    await chat_module.hook_persist_turn(_params_with_batch(batch))
    stored = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", INSTANCE_ID
    )
    msgs = stored.get("messages", [])
    assert len(msgs) == 1
    assert msgs[0]["content"] == "real message"


@pytest.mark.asyncio
async def test_silent_batch_preserves_attachments_per_row(chat_module):
    batch = [
        {
            "event_id": "$evt1",
            "sender_id": "@a:h",
            "sender_name": "Alice",
            "content": "check this out",
            "attachments": [
                {"mime_type": "image/png", "path": "/ws/a/img.png", "size_bytes": 42},
            ],
        },
    ]
    await chat_module.hook_persist_turn(_params_with_batch(batch))
    stored = await chat_module.event_memory_module.search_instance_json_format_memory(
        "ChatModule", INSTANCE_ID
    )
    msgs = stored.get("messages", [])
    assert len(msgs) == 1
    assert msgs[0]["attachments"] == [
        {"mime_type": "image/png", "path": "/ws/a/img.png", "size_bytes": 42},
    ]


@pytest.mark.asyncio
async def test_no_batch_field_leaves_existing_path_untouched(chat_module):
    """No batch_messages in extra_data → normal path runs (which needs
    io_data / final_output). This test simply verifies we don't crash
    when batch_messages is absent — the batch guard's `isinstance(list)`
    check must not confuse other extra_data shapes."""
    ctx_data = ContextData(
        agent_id=AGENT_ID,
        user_id=USER_ID,
        input_content="hello",
        extra_data={"channel_tag": {"channel": "narramessenger_matrix"}},
    )
    params = HookAfterExecutionParams(
        execution_ctx=HookExecutionContext(
            event_id="evt_normal",
            agent_id=AGENT_ID,
            user_id=USER_ID,
            working_source=WorkingSource.CHAT,
        ),
        io_data=HookIOData(input_content="hello", final_output="hi back"),
        trace=HookExecutionTrace(event_log=[], agent_loop_response=[]),
        ctx_data=ctx_data,
    )
    # Should not raise.
    await chat_module.hook_persist_turn(params)
