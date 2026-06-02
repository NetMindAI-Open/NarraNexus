"""
@file_name: test_channel_debounce_merger_attachments.py
@date: 2026-05-20
@description: Phase 1a — ChannelDebounceMerger concatenates
``raw["attachment_refs"]`` from every bursted ParsedMessage onto the
merged result, and leaves the input messages unchanged (immutability).

Mirrors the shape of ``test_channel_debounce_merger.py`` — single
window flush, multi-message merge — but exercises only the new refs
behaviour. Existing tests continue to cover text/media_urls.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xyz_agent_context.channel.channel_debounce_merger import ChannelDebounceMerger
from xyz_agent_context.schema.parsed_message import ParsedMessage


def _msg_with_refs(
    message_id: str,
    refs: list[dict[str, Any]],
    *,
    chat_id: str = "c1",
    sender_id: str = "u1",
    content: str = "",
) -> ParsedMessage:
    return ParsedMessage(
        message_id=message_id,
        chat_id=chat_id,
        sender_id=sender_id,
        content=content,
        raw={"attachment_refs": refs} if refs else {},
    )


@pytest.mark.asyncio
async def test_burst_concatenates_attachment_refs_in_order() -> None:
    """Two bursted messages from the same sender → merged.raw has both
    refs lists concatenated in arrival order."""
    merger = ChannelDebounceMerger(window_ms=80)
    received: list[ParsedMessage] = []

    async def cb(merged: ParsedMessage) -> None:
        received.append(merged)

    ref_a = {"platform_ref": "fA", "original_name": "a.pdf", "mime_hint": "application/pdf"}
    ref_b = {"platform_ref": "fB", "original_name": "b.jpg", "mime_hint": "image/jpeg"}

    await merger.submit(_msg_with_refs("m1", [ref_a], content="here"), cb)
    await merger.submit(_msg_with_refs("m2", [ref_b], content="and here"), cb)

    await asyncio.sleep(0.2)

    assert len(received) == 1
    refs = received[0].raw.get("attachment_refs")
    assert refs == [ref_a, ref_b]
    # Body merge still works alongside refs concat.
    assert received[0].content == "here\nand here"


@pytest.mark.asyncio
async def test_merge_does_not_mutate_inputs() -> None:
    """Inputs must not be mutated — earlier merger versions reused
    ``latest.raw`` in place which leaked into dedup-store entries."""
    merger = ChannelDebounceMerger(window_ms=80)
    received: list[ParsedMessage] = []

    async def cb(merged: ParsedMessage) -> None:
        received.append(merged)

    ref_a = {"platform_ref": "fA", "original_name": "a.pdf", "mime_hint": "application/pdf"}
    ref_b = {"platform_ref": "fB", "original_name": "b.jpg", "mime_hint": "image/jpeg"}
    msg_a = _msg_with_refs("m1", [ref_a], content="x")
    msg_b = _msg_with_refs("m2", [ref_b], content="y")
    # Snapshot the input raw dicts BEFORE submitting.
    raw_a_before = dict(msg_a.raw)
    raw_b_before = dict(msg_b.raw)

    await merger.submit(msg_a, cb)
    await merger.submit(msg_b, cb)
    await asyncio.sleep(0.2)

    assert len(received) == 1
    # Inputs are unchanged.
    assert msg_a.raw == raw_a_before
    assert msg_b.raw == raw_b_before
    # Merged is a different dict from the latest input.
    assert received[0].raw is not msg_b.raw


@pytest.mark.asyncio
async def test_single_message_with_refs_passes_through_unchanged() -> None:
    """No merge needed — single message returns as-is, refs preserved."""
    merger = ChannelDebounceMerger(window_ms=60)
    received: list[ParsedMessage] = []

    async def cb(merged: ParsedMessage) -> None:
        received.append(merged)

    ref = {"platform_ref": "fX", "original_name": "x.pdf", "mime_hint": "application/pdf"}
    await merger.submit(_msg_with_refs("m1", [ref], content="solo"), cb)
    await asyncio.sleep(0.15)

    assert len(received) == 1
    assert received[0].raw.get("attachment_refs") == [ref]


@pytest.mark.asyncio
async def test_mixed_burst_with_some_refless_messages() -> None:
    """Messages without refs don't break the merge — only contributing
    messages add to the merged refs list."""
    merger = ChannelDebounceMerger(window_ms=80)
    received: list[ParsedMessage] = []

    async def cb(merged: ParsedMessage) -> None:
        received.append(merged)

    ref = {"platform_ref": "fA", "original_name": "a.pdf", "mime_hint": "application/pdf"}

    # m1: text only (no refs). m2: ref + caption. m3: text only.
    await merger.submit(_msg_with_refs("m1", [], content="setup"), cb)
    await merger.submit(_msg_with_refs("m2", [ref], content="see this"), cb)
    await merger.submit(_msg_with_refs("m3", [], content="thoughts?"), cb)
    await asyncio.sleep(0.2)

    assert len(received) == 1
    assert received[0].raw.get("attachment_refs") == [ref]
    assert received[0].content == "setup\nsee this\nthoughts?"
