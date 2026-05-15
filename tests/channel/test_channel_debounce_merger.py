"""
ChannelDebounceMerger — buffer + window flush + merge semantics.
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.channel.channel_debounce_merger import ChannelDebounceMerger
from xyz_agent_context.schema.parsed_message import ParsedMessage


def _msg(message_id: str, *, chat_id="c1", sender_id="u1", content="", media_urls=None):
    return ParsedMessage(
        message_id=message_id,
        chat_id=chat_id,
        sender_id=sender_id,
        content=content,
        media_urls=list(media_urls or []),
    )


@pytest.mark.asyncio
async def test_single_message_flushes_after_window():
    merger = ChannelDebounceMerger(window_ms=100)
    received: list[ParsedMessage] = []

    async def cb(merged):
        received.append(merged)

    await merger.submit(_msg("m1", content="hi"), cb)
    # Wait past the window
    await asyncio.sleep(0.2)
    assert len(received) == 1
    assert received[0].content == "hi"


@pytest.mark.asyncio
async def test_two_messages_within_window_merge_into_one_callback():
    """Submit twice within the window — only one callback with concatenated content."""
    merger = ChannelDebounceMerger(window_ms=200)
    received: list[ParsedMessage] = []

    async def cb(merged):
        received.append(merged)

    await merger.submit(_msg("m1", content="hello"), cb)
    await asyncio.sleep(0.05)  # well within window
    await merger.submit(_msg("m2", content="world"), cb)
    await asyncio.sleep(0.4)  # past the window

    assert len(received) == 1
    assert received[0].content == "hello\nworld"
    # Last message metadata wins
    assert received[0].message_id == "m2"


@pytest.mark.asyncio
async def test_two_messages_after_window_fire_separately():
    merger = ChannelDebounceMerger(window_ms=80)
    received: list[ParsedMessage] = []

    async def cb(merged):
        received.append(merged)

    await merger.submit(_msg("m1", content="first"), cb)
    await asyncio.sleep(0.2)  # past window — first should already have flushed
    await merger.submit(_msg("m2", content="second"), cb)
    await asyncio.sleep(0.2)

    assert len(received) == 2
    assert received[0].content == "first"
    assert received[1].content == "second"


@pytest.mark.asyncio
async def test_different_chat_ids_do_not_merge():
    merger = ChannelDebounceMerger(window_ms=200)
    received: list[ParsedMessage] = []

    async def cb(merged):
        received.append(merged)

    await merger.submit(_msg("m1", chat_id="cA", content="hi A"), cb)
    await merger.submit(_msg("m2", chat_id="cB", content="hi B"), cb)
    await asyncio.sleep(0.4)

    contents = sorted(m.content for m in received)
    assert contents == ["hi A", "hi B"]


@pytest.mark.asyncio
async def test_different_senders_do_not_merge():
    merger = ChannelDebounceMerger(window_ms=200)
    received: list[ParsedMessage] = []

    async def cb(merged):
        received.append(merged)

    await merger.submit(_msg("m1", sender_id="alice", content="from alice"), cb)
    await merger.submit(_msg("m2", sender_id="bob", content="from bob"), cb)
    await asyncio.sleep(0.4)

    senders = sorted(m.sender_id for m in received)
    assert senders == ["alice", "bob"]


@pytest.mark.asyncio
async def test_merge_concatenates_media_urls():
    merger = ChannelDebounceMerger(window_ms=120)
    received: list[ParsedMessage] = []

    async def cb(merged):
        received.append(merged)

    await merger.submit(
        _msg("m1", content="a", media_urls=["http://example.com/1"]),
        cb,
    )
    await merger.submit(
        _msg("m2", content="b", media_urls=["http://example.com/2"]),
        cb,
    )
    await asyncio.sleep(0.3)

    assert len(received) == 1
    assert received[0].media_urls == [
        "http://example.com/1",
        "http://example.com/2",
    ]


@pytest.mark.asyncio
async def test_flush_all_drains_pending_buffers():
    merger = ChannelDebounceMerger(window_ms=10_000)  # very long window
    received: list[ParsedMessage] = []

    async def cb(merged):
        received.append(merged)

    await merger.submit(_msg("m1", content="pending"), cb)
    # Without flush_all the long window would never flush
    await merger.flush_all(cb)
    # Give the flush coroutine room to complete
    await asyncio.sleep(0.05)

    assert len(received) == 1


def test_merger_rejects_zero_window():
    with pytest.raises(ValueError):
        ChannelDebounceMerger(window_ms=0)


@pytest.mark.asyncio
async def test_merge_does_not_mutate_input_messages():
    """The merger MUST return a fresh ParsedMessage. Mutating the last
    input leaks into anything else holding a reference (notably the
    dedup store's hot cache) and, on a shutdown timer/flush_all race,
    can cause the same payload to be processed twice."""
    merger = ChannelDebounceMerger(window_ms=120)
    received: list[ParsedMessage] = []

    async def cb(merged):
        received.append(merged)

    m1 = _msg("m1", content="hello", media_urls=["http://x/1"])
    m2 = _msg("m2", content="world", media_urls=["http://x/2"])

    await merger.submit(m1, cb)
    await merger.submit(m2, cb)
    await asyncio.sleep(0.3)

    assert len(received) == 1
    merged = received[0]
    # Merged result carries the combined payload.
    assert merged.content == "hello\nworld"
    assert merged.media_urls == ["http://x/1", "http://x/2"]
    # Critical invariant: neither input was mutated.
    assert m1.content == "hello"
    assert m1.media_urls == ["http://x/1"]
    assert m2.content == "world"
    assert m2.media_urls == ["http://x/2"]
    # And the returned object is not the same instance as m2 (the
    # historical mutation bug returned `latest` directly).
    assert merged is not m2
