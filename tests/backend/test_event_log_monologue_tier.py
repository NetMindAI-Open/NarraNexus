"""
@file_name: test_event_log_monologue_tier.py
@author: NarraNexus
@date: 2026-08-30

Replay-side net for the interim-narration tier (design A′).

NexusPower's own assistant text ("monologue") streams as thinking but is a
different tier from provider chain-of-thought: the frontend renders it at the
"progress" tier while CoT keeps receding. The distinction is written to
``events.event_log`` by ``execution_state.record_thinking`` and, until this
change, was dropped at the API layer — so a turn looked different live than it
did after a refresh, breaking the equivalence ``segmentTurn`` is built on.

The batcher upstream coalesces the DISPLAY stream (monologue + CoT mixed, iron
rule #16 verbatim) while ``monologue`` carries only the monologue SUBSET. So a
persisted step is progress-tier only when it is tier-PURE — ``monologue`` is
non-empty and equals ``content``. A mixed step has no recoverable split point
(we have the union and the subset, never the positions), so it falls back to
plain thinking: never brighten CoT, never invent a boundary.
"""
from __future__ import annotations

from narranexus_plugins.chat_module.chat_history_timeline import build_event_timeline


def _thinking(content: str, monologue: str | None = None) -> dict:
    """One persisted thinking step, shaped like execution_state writes it."""
    step: dict = {"type": "thinking", "content": content}
    if monologue is not None:
        step["monologue"] = monologue
    return step


def _tool_call(name: str = "read_file") -> dict:
    return {"type": "tool_call", "tool_name": name, "arguments": {}, "tool_call_id": "c1"}


def test_pure_monologue_step_is_tagged_progress_tier():
    timeline = build_event_timeline([_thinking("Checking the plugin state.", "Checking the plugin state.")], {})

    assert [(e.type, e.content, e.monologue) for e in timeline] == [
        ("thinking", "Checking the plugin state.", True)
    ]


def test_provider_cot_step_is_not_progress_tier():
    """Provider chain-of-thought carries no monologue subset — stays receded."""
    timeline = build_event_timeline([_thinking("The user probably means...")], {})

    assert [(e.type, e.monologue) for e in timeline] == [("thinking", False)]


def test_adjacent_monologue_and_cot_do_not_merge():
    """A tier change is a block boundary.

    Without this the coalescer could glue a monologue step onto a CoT step and
    the merged entry would carry ONE tier for two tiers of text — either
    dimming the narration or brightening the scratchpad.
    """
    timeline = build_event_timeline(
        [
            _thinking("Official support confirmed.", "Official support confirmed."),
            _thinking("Now, which of the two paths..."),
            _thinking("Checking your machine next.", "Checking your machine next."),
        ],
        {},
    )

    assert [(e.content, e.monologue) for e in timeline] == [
        ("Official support confirmed.", True),
        ("Now, which of the two paths...", False),
        ("Checking your machine next.", True),
    ]


def test_same_tier_consecutive_steps_still_coalesce():
    """The existing coalescing (50 tiny italic blocks -> one) is not regressed."""
    timeline = build_event_timeline(
        [_thinking("Check", "Check"), _thinking("ing the ", "ing the "), _thinking("state.", "state.")],
        {},
    )

    assert [(e.content, e.monologue) for e in timeline] == [("Checking the state.", True)]


def test_mixed_step_falls_back_to_plain_thinking():
    """content is the union, monologue only a subset -> no recoverable split."""
    timeline = build_event_timeline(
        [_thinking("CoT preamble. Then I speak.", "Then I speak.")],
        {},
    )

    assert [(e.content, e.monologue) for e in timeline] == [
        ("CoT preamble. Then I speak.", False)
    ]


def test_legacy_step_without_monologue_key_is_plain_thinking():
    """Rows persisted before this field existed must not crash or change tier."""
    timeline = build_event_timeline([{"type": "thinking", "content": "old row"}], {})

    assert [(e.content, e.monologue) for e in timeline] == [("old row", False)]


def test_tool_call_still_breaks_the_thinking_block():
    """Pre-existing boundary behaviour is untouched by the tier split."""
    timeline = build_event_timeline(
        [
            _thinking("Looking now.", "Looking now."),
            _tool_call(),
            _thinking("Found it.", "Found it."),
        ],
        {},
    )

    assert [e.type for e in timeline] == ["thinking", "tool_call", "thinking"]
    assert [e.monologue for e in timeline if e.type == "thinking"] == [True, True]
