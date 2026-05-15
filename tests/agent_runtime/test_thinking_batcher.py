"""
@file_name: test_thinking_batcher.py
@author: Bin Liang
@date: 2026-05-13
@description: Unit tests for `_ThinkingBatcher` — Phase B WS-tier coalescing.
"""
from __future__ import annotations

import time

import pytest

from xyz_agent_context.agent_runtime._thinking_batcher import _ThinkingBatcher


def test_single_small_chunk_buffered_no_flush():
    b = _ThinkingBatcher()
    out = b.append_thinking("a")
    assert out is None
    assert b.has_pending()


def test_large_chunk_flushes_immediately():
    """A single chunk that already exceeds FLUSH_CHARS gets emitted on
    arrival — no need to wait for the 100 ms window."""
    b = _ThinkingBatcher()
    big = "x" * 600
    out = b.append_thinking(big)
    assert out == big
    assert not b.has_pending()


def test_accumulated_chunks_reach_chars_threshold():
    """Many small chunks add up; once ≥FLUSH_CHARS the NEXT append
    returns the concatenated payload."""
    b = _ThinkingBatcher()
    for _ in range(99):
        assert b.append_thinking("y") is None  # 99 chars buffered
    # 99 chars accumulated; 401 more brings us to 500 → triggers
    out = b.append_thinking("y" * 401)
    assert out == "y" * 500
    assert not b.has_pending()


def test_time_threshold_triggers_flush():
    """After FLUSH_MS elapsed since the first chunk, the next append
    returns the buffered content."""
    b = _ThinkingBatcher()
    b.append_thinking("hello")
    # Simulate >100 ms passing
    b._last_flush_ts -= 0.2  # backdate by 200 ms
    out = b.append_thinking(" world")
    # Both "hello" and " world" are part of the same buffer flushed by
    # time threshold (append adds to buffer THEN checks threshold).
    assert out == "hello world"


def test_flush_ws_explicit_returns_residual():
    """Caller-driven flush returns whatever was buffered."""
    b = _ThinkingBatcher()
    b.append_thinking("partial")
    out = b.flush_ws()
    assert out == "partial"
    assert not b.has_pending()


def test_flush_ws_on_empty_returns_none():
    b = _ThinkingBatcher()
    assert b.flush_ws() is None


def test_empty_input_is_noop():
    b = _ThinkingBatcher()
    assert b.append_thinking("") is None
    assert b.append_thinking(None or "") is None  # None-safe via falsy check
    assert not b.has_pending()


def test_content_preserved_verbatim_across_many_chunks():
    """The critical correctness property: no character lost, no
    character reordered, regardless of how the batcher decides to
    chunk the WS frames."""
    b = _ThinkingBatcher()
    source = "The quick brown fox jumps over the lazy dog. " * 50
    # Feed character by character — pathological case
    received: list[str] = []
    for ch in source:
        out = b.append_thinking(ch)
        if out is not None:
            received.append(out)
    # Flush residual
    residual = b.flush_ws()
    if residual:
        received.append(residual)

    assert "".join(received) == source
    # Should have produced far fewer than len(source) frames
    assert len(received) < len(source) / 5  # at least 5× reduction


def test_flush_resets_window_for_next_segment():
    """After a flush, the next chunk seeds a fresh time window."""
    b = _ThinkingBatcher()
    b.append_thinking("first " * 80)  # 480 chars
    # Not yet ≥500 → buffered
    assert b.has_pending()
    big = b.append_thinking("more more")  # 489 → 498 still under, no flush
    assert big is None  # under 500
    # Force flush
    out = b.flush_ws()
    assert out == "first " * 80 + "more more"
    # New chunk — should NOT immediately trigger time-based flush
    fresh = b.append_thinking("hi")
    assert fresh is None  # window just started
