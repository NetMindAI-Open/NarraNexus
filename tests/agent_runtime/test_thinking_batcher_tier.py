"""
@file_name: test_thinking_batcher_tier.py
@author: NarraNexus
@date: 2026-08-30
@description: The batcher must never coalesce across a tier switch.

Why this exists — reproduced live on 2026-08-30, DeepSeek-V4-Pro through
nexus_power. The batcher flushes on ~100 ms / 500 chars and, before this,
only on non-thinking events; a monologue/CoT switch did NOT flush. So the
batch straddling that switch carried CoT plus the first characters of the
narration, and the frontend's tier rule (subset == whole) called it CoT:

    frame 41  content = "...Silence is correct here." + "There"   monologue = "There"
    frame 42  content = "'s no new user message in this turn..."  (pure)

frame 41 rendered receded, frame 42 as narration — one sentence torn at
"There" | "'s no new user message", mid-word, at whatever byte the 100 ms
window happened to land on.

A tier switch is now a flush, so every emitted batch is tier-PURE and a
boundary can only fall where the tier genuinely changed — never inside a
content frame.

Iron rule #16: this must not cost the coalescing it exists for. A stream
that never switches tier flushes exactly as it did before; the only extra
batches are at real transitions. Pinned below.
"""
from __future__ import annotations

from narranexus.platform.agent_runtime._thinking_batcher import _ThinkingBatcher


def test_tier_switch_flushes_and_batch_is_pure():
    """The reproduced case: CoT then narration inside one time window."""
    b = _ThinkingBatcher()

    assert b.append_thinking("Silence is correct here.", False) is None
    out = b.append_thinking("There", True)

    # The CoT closes at the switch — the narration is NOT welded onto it.
    assert out == "Silence is correct here."
    assert b.flushed_tier is False

    # And the narration is buffered on its own, as its own tier.
    rest = b.flush_ws()
    assert rest == "There"
    assert b.flushed_tier is True


def test_no_boundary_ever_falls_inside_a_content_frame():
    """Every emitted batch is exactly a concatenation of whole inputs.

    The split was mid-WORD, so the invariant worth pinning is not "two
    batches" but "no batch ever contains part of a chunk".
    """
    chunks = [
        ("thinking about it. ", False),
        ("more thinking. ", False),
        ("There", True),
        ("'s no new user message.", True),
        ("back to thinking", False),
    ]
    b = _ThinkingBatcher()
    emitted = []
    for text, tier in chunks:
        out = b.append_thinking(text, tier)
        if out is not None:
            emitted.append((out, b.flushed_tier))
    residual = b.flush_ws()
    if residual is not None:
        emitted.append((residual, b.flushed_tier))

    # Content preserved verbatim, in order (iron rule #16).
    assert "".join(text for text, _ in emitted) == "".join(c for c, _ in chunks)

    # Every batch is a whole number of input chunks — no chunk was split.
    remaining = list(chunks)
    for text, _tier in emitted:
        consumed = ""
        while consumed != text:
            assert remaining, f"batch {text!r} does not align to chunk boundaries"
            consumed += remaining.pop(0)[0]
            assert text.startswith(consumed) or consumed == text
    assert not remaining

    # And every batch carries ONE tier.
    assert [tier for _, tier in emitted] == [False, True, False]


def test_pure_stream_coalescing_is_not_regressed():
    """A stream that never switches tier batches exactly as before.

    This is the whole reason the batcher exists (#16: cut frames, never
    content), so the tier split must cost nothing when there is no switch.
    """
    plain = _ThinkingBatcher()
    tiered = _ThinkingBatcher()

    plain_out, tiered_out = [], []
    for i in range(40):
        chunk = f"chunk{i:03d}-"
        if (o := plain.append_thinking(chunk)) is not None:
            plain_out.append(o)
        if (o := tiered.append_thinking(chunk, True)) is not None:
            tiered_out.append(o)

    # Same number of frames, same boundaries — the tier is uniform, so the
    # only triggers that fired are the pre-existing size/time ones.
    assert plain_out == tiered_out


def test_switch_flush_ignores_size_and_time_triggers():
    """A tier switch flushes even on a tiny, fast buffer.

    Otherwise the mixed batch survives exactly in the case that produced
    the bug: a short CoT tail immediately followed by narration.
    """
    b = _ThinkingBatcher()
    b.append_thinking("x", False)  # 1 char, no time elapsed
    out = b.append_thinking("y", True)

    assert out == "x"
    assert b.flushed_tier is False


def test_switch_on_empty_buffer_does_not_emit():
    """Nothing buffered means nothing to close — the tier just becomes the
    new one, and no empty frame is pushed at the user."""
    b = _ThinkingBatcher()
    assert b.append_thinking("narration", True) is None
    assert b.has_pending()


def test_flushed_tier_defaults_false_before_any_flush():
    b = _ThinkingBatcher()
    assert b.flushed_tier is False
