"""
@file_name: test_event_adapter.py
@author: Bin Liang
@date: 2026-07-29
@description: LegacyEventAdapter monologue stamping.

text_delta (the framework's assistant text = monologue) must carry
``monologue: true`` on its legacy thinking_item so the platform can
route it into ``final_output`` (reasoning persistence, fallback
decisions). thinking_delta (provider CoT) must NOT carry the flag —
CoT never enters final_output on any driver.
"""
from __future__ import annotations

from narranexus_plugins.frameworks_nexus_power.core.contracts.events import (
    TYPE_ERROR,
    TYPE_TEXT_DELTA,
    TYPE_THINKING_DELTA,
    LoopEvent,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.event_adapter import (
    LegacyEventAdapter,
)


def _translate_one(event_type: str) -> dict:
    event = LoopEvent(
        track="ui",
        seq=0,
        type=event_type,
        payload={"text": "hello", "monologue": True},
    )
    out = LegacyEventAdapter().translate(event)
    assert len(out) == 1
    return out[0]["item"]


def test_text_delta_thinking_item_is_stamped_monologue():
    item = _translate_one(TYPE_TEXT_DELTA)
    assert item["type"] == "thinking_item"
    assert item["content"] == "hello"
    assert item.get("monologue") is True


def test_thinking_delta_stays_unstamped():
    item = _translate_one(TYPE_THINKING_DELTA)
    assert item["type"] == "thinking_item"
    assert item["content"] == "hello"
    assert not item.get("monologue")


def test_type_error_defaults_to_fatal_when_the_loop_did_not_say():
    """``fatal`` is framework-reported: it means "this turn is terminal
    AND nothing was delivered this turn", not merely "this turn ended".
    loop.py's ``_fail`` always sets the key explicitly (from
    ``not self._turn_delivered()``), but if a payload arrives without one
    the adapter must default to the conservative ``True`` -- every
    TYPE_ERROR this framework's loop emits closes the turn with
    EndReason.ERROR right after, unlike claude/codex's inline API errors
    which can be absorbed mid-stream (B-05/#127). ``fatal: true`` tells
    response_processor to classify it severity="fatal" instead of the
    generic "recoverable" default."""
    event = LoopEvent(
        track="ui",
        seq=0,
        type=TYPE_ERROR,
        payload={"error_type": "output_truncated", "message": "boom", "retryable": False},
    )
    out = LegacyEventAdapter().translate(event)
    assert len(out) == 1
    assert out[0]["data"]["fatal"] is True


def test_type_error_fatal_false_is_passed_through():
    """When loop.py explicitly reports ``fatal: False`` (the turn already
    delivered output (an expressive call, or plain text on a turn with no
    expression tool) before this failure
    landed), the adapter must not override it back to True -- that
    would erase response_processor's ability to tell
    "recovered_after_reply" apart from a genuinely empty run."""
    event = LoopEvent(
        track="ui",
        seq=0,
        type=TYPE_ERROR,
        payload={
            "error_type": "output_truncated",
            "message": "boom",
            "retryable": False,
            "fatal": False,
        },
    )
    out = LegacyEventAdapter().translate(event)
    assert len(out) == 1
    assert out[0]["data"]["fatal"] is False
