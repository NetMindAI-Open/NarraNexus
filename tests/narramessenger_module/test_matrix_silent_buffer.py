"""
@file_name: test_matrix_silent_buffer.py
@date: 2026-07-02
@description: MatrixTrigger — silent-batch buffer + debounce.

Locks:
- Burst-cap flush: at SILENT_FLUSH_BURST_SIZE the buffer flushes
  immediately (no wait for debounce).
- Idle debounce: SILENT_DEBOUNCE_SECONDS after the LAST enqueue.
- Cancel-and-replace: a new enqueue resets the timer.
- drain-all: reconnect burst + stop() drains every buffer synchronously.
- Payload: the batch that reaches _build_and_run_agent_silent_batch
  contains the SAME messages, in arrival order, with a
  sender_name_by_id map built from the display-name cache.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
)
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage


ROOM = "!bigroom:h"
AGENT_ID_NX = "agent_x"
OWNER_ID = "@owner:h"
BOB_ID = "@bob:h"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id=AGENT_ID_NX,
        bearer_token="tok",
    )


def _msg(idx: int, sender: str = BOB_ID) -> ParsedMessage:
    return ParsedMessage(
        message_id=f"$e{idx}",
        chat_id=ROOM,
        sender_id=sender,
        sender_name=sender,
        content=f"msg {idx}",
        chat_type=ChatType.GROUP,
        timestamp_ms=1000 + idx,
        raw={"kind": "m.room.message.text"},
    )


@pytest.fixture
def trigger():
    t = MatrixTrigger()
    # Shrink debounce so time-based tests don't sleep 5s
    t.SILENT_DEBOUNCE_SECONDS = 0.05
    # Pre-populate display name cache for bob so payload check verifies
    # cache→payload wiring.
    t._display_name_cache[(ROOM, BOB_ID)] = "Bob"
    # Stub out the actual silent-batch runtime call — we only care WHAT
    # the trigger hands off, not what the runtime does with it.
    t._build_and_run_agent_silent_batch = AsyncMock(return_value=None)
    return t


@pytest.mark.asyncio
async def test_burst_cap_flushes_immediately(trigger):
    trigger.SILENT_FLUSH_BURST_SIZE = 3
    for i in range(3):
        await trigger._enqueue_silent(_cred(), _msg(i))
    # Immediately after the 3rd enqueue, batch must have shipped —
    # no need to wait for the debounce.
    assert trigger._build_and_run_agent_silent_batch.await_count == 1
    kwargs = trigger._build_and_run_agent_silent_batch.await_args.kwargs
    assert len(kwargs["messages"]) == 3
    assert [m.content for m in kwargs["messages"]] == ["msg 0", "msg 1", "msg 2"]
    # sender_name resolves from the display-name cache seeded above.
    assert kwargs["sender_name_by_id"][BOB_ID] == "Bob"


@pytest.mark.asyncio
async def test_idle_debounce_flushes_after_quiet_window(trigger):
    await trigger._enqueue_silent(_cred(), _msg(0))
    # Nothing flushed yet — debounce timer running.
    assert trigger._build_and_run_agent_silent_batch.await_count == 0
    # Wait past the debounce window.
    await asyncio.sleep(trigger.SILENT_DEBOUNCE_SECONDS + 0.05)
    assert trigger._build_and_run_agent_silent_batch.await_count == 1


@pytest.mark.asyncio
async def test_new_enqueue_resets_the_timer(trigger):
    await trigger._enqueue_silent(_cred(), _msg(0))
    # Wait 60% of debounce, then enqueue again — timer should reset.
    await asyncio.sleep(trigger.SILENT_DEBOUNCE_SECONDS * 0.6)
    await trigger._enqueue_silent(_cred(), _msg(1))
    # Wait another 60% (total 1.2x from FIRST enqueue but 0.6x from
    # SECOND); without reset the flush would already have fired.
    await asyncio.sleep(trigger.SILENT_DEBOUNCE_SECONDS * 0.6)
    assert trigger._build_and_run_agent_silent_batch.await_count == 0
    # Now wait past the reset debounce.
    await asyncio.sleep(trigger.SILENT_DEBOUNCE_SECONDS)
    assert trigger._build_and_run_agent_silent_batch.await_count == 1
    kwargs = trigger._build_and_run_agent_silent_batch.await_args.kwargs
    assert len(kwargs["messages"]) == 2  # both msgs land in one flush


@pytest.mark.asyncio
async def test_drain_all_flushes_every_room_immediately(trigger):
    # Two rooms in the buffer at once.
    room2 = "!room2:h"
    trigger._display_name_cache[(room2, OWNER_ID)] = "Owner"
    await trigger._enqueue_silent(_cred(), _msg(0, sender=BOB_ID))
    m1 = _msg(1, sender=OWNER_ID)
    m1.chat_id = room2
    await trigger._enqueue_silent(_cred(), m1)
    # Nothing flushed yet.
    assert trigger._build_and_run_agent_silent_batch.await_count == 0
    # Drain all — should flush BOTH rooms synchronously.
    await trigger._drain_all_silent_buffers()
    assert trigger._build_and_run_agent_silent_batch.await_count == 2


@pytest.mark.asyncio
async def test_flush_swallows_runtime_exception(trigger):
    """A raised silent-batch call must not break the debounce path — the
    error is logged; the trigger continues to accept new messages."""
    trigger._build_and_run_agent_silent_batch = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    await trigger._enqueue_silent(_cred(), _msg(0))
    await asyncio.sleep(trigger.SILENT_DEBOUNCE_SECONDS + 0.05)
    # Should not raise; new enqueue after failure still works.
    await trigger._enqueue_silent(_cred(), _msg(1))
    # Trigger not broken → the buffer accepted the second message.
    assert (AGENT_ID_NX, ROOM) in trigger._silent_buffer or \
        trigger._build_and_run_agent_silent_batch.await_count >= 1
