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

from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    TYPE_TEXT_DELTA,
    TYPE_THINKING_DELTA,
    LoopEvent,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.event_adapter import (
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
