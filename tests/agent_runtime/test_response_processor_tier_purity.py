"""
@file_name: test_response_processor_tier_purity.py
@author: NarraNexus
@date: 2026-08-30
@description: End-to-end: an interleaved thinking stream yields tier-PURE frames.

The batcher's own tests pin the buffering rule; this pins what actually
reaches the frontend — ``AgentThinking`` frames whose ``monologue`` field is
either the whole ``thinking_content`` or empty, never a partial subset.

That subset case is what tore a sentence in half. Reproduced live on
2026-08-30 (DeepSeek-V4-Pro via nexus_power), frames 41-42 of the run::

    content = "...Silence is correct here." + "There"   monologue = "There"
    content = "'s no new user message in this turn..."  (pure)

The frontend's rule (``monologue == thinking_content``) called frame 41 CoT,
so "There" rendered receded and "'s no new user message…" opened a promoted
block — a mid-word tear at whatever byte the 100 ms window landed on.

The item shapes below are what ``event_adapter`` actually emits: provider CoT
is a thinking_item with no ``monologue`` key; NexusPower's own text is a
thinking_item stamped ``monologue: True``.
"""
from __future__ import annotations

from narranexus.platform.agent_runtime.execution_state import ExecutionState
from narranexus.platform.agent_runtime.response_processor import (
    ResponseProcessor,
    ResponseType,
)


def _cot(content: str) -> dict:
    """Provider chain-of-thought: no monologue stamp (event_adapter:71-89)."""
    return {
        "type": "run_item_stream_event",
        "item": {"type": "thinking_item", "content": content},
    }


def _narration(content: str) -> dict:
    """NexusPower's own assistant text, stamped by event_adapter."""
    return {
        "type": "run_item_stream_event",
        "item": {"type": "thinking_item", "content": content, "monologue": True},
    }


def _drain(events) -> list:
    """Feed events through a processor and collect the thinking frames."""
    proc = ResponseProcessor()
    state = ExecutionState()
    frames = []
    for ev in events:
        for res in proc.process(ev, state):
            if res.type is ResponseType.THINKING:
                frames.append(res.message)
    for res in proc.flush_pending(state):
        if res.type is ResponseType.THINKING:
            frames.append(res.message)
    return frames


def test_interleaved_stream_yields_only_tier_pure_frames():
    """Every frame is all-monologue or all-CoT — never a partial subset."""
    frames = _drain([
        _cot("Silence is correct here."),
        _narration("There"),
        _narration("'s no new user message in this turn."),
    ])

    for f in frames:
        assert f.monologue in ("", f.thinking_content), (
            f"partial subset leaked: content={f.thinking_content!r} "
            f"monologue={f.monologue!r}"
        )


def test_the_reproduced_split_is_gone():
    """The exact live case: two frames, one per tier, sentence intact."""
    frames = _drain([
        _cot("Silence is correct here."),
        _narration("There"),
        _narration("'s no new user message in this turn."),
    ])

    tiers = [(f.thinking_content, bool(f.monologue)) for f in frames]
    assert tiers == [
        ("Silence is correct here.", False),
        ("There's no new user message in this turn.", True),
    ]


def test_no_frame_boundary_falls_inside_a_chunk():
    """Frames align to whole input chunks, and content is verbatim (#16)."""
    chunks = [
        ("weighing the options. ", False),
        ("still weighing. ", False),
        ("Reading the config now.", True),
        (" Then I'll patch it.", True),
        ("hmm, patching is risky", False),
    ]
    frames = _drain([
        _narration(text) if tier else _cot(text) for text, tier in chunks
    ])

    assert "".join(f.thinking_content for f in frames) == "".join(c for c, _ in chunks)

    remaining = list(chunks)
    for f in frames:
        consumed = ""
        while consumed != f.thinking_content:
            assert remaining, f"frame {f.thinking_content!r} splits a chunk"
            consumed += remaining.pop(0)[0]
            assert f.thinking_content.startswith(consumed)
    assert not remaining

    assert [bool(f.monologue) for f in frames] == [False, True, False]


def test_pure_cot_stream_carries_no_monologue():
    frames = _drain([_cot("a"), _cot("b"), _cot("c")])

    assert [f.monologue for f in frames] == [""] * len(frames)
    assert "".join(f.thinking_content for f in frames) == "abc"


def test_pure_narration_stream_is_fully_monologue():
    frames = _drain([_narration("a"), _narration("b")])

    for f in frames:
        assert f.monologue == f.thinking_content
    assert "".join(f.thinking_content for f in frames) == "ab"


def test_final_output_still_receives_only_the_narration():
    """record_thinking appends `monologue` to final_output — the tier split
    must not change which characters land there (collect_run relays it)."""
    proc = ResponseProcessor()
    state = ExecutionState()
    for ev in [_cot("thinking hard. "), _narration("Reading the file."), _cot(" more thought")]:
        for res in proc.process(ev, state):
            state = proc.apply_state_update(state, res)
    for res in proc.flush_pending(state):
        state = proc.apply_state_update(state, res)

    assert state.final_output == "Reading the file."
