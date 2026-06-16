"""
@file_name: test_claude_reasoning_mapping.py
@author: Bin Liang
@date: 2026-06-10
@description: Claude adapter mapping for the framework-neutral reasoning
params (SlotConfig.thinking / SlotConfig.reasoning_effort).

The slot stores neutral values; this adapter owns the Claude dialect:

  thinking "on"  -> ClaudeAgentOptions.thinking = {"type": "adaptive"}
  thinking "off" -> ClaudeAgentOptions.thinking = {"type": "disabled"}
  thinking ""    -> option absent (CLI decides — today's behavior)
  effort low/medium/high/max -> ClaudeAgentOptions.effort (1:1, Claude
                                supports the full neutral vocabulary)
  effort ""      -> option absent

Out-of-vocabulary values cannot normally reach here (SlotConfig validates),
but the mapper must still degrade to "absent + warning" rather than raise —
a bad tuning knob must never take the agent loop down (defensive only).
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
    _resolve_reasoning_options,
)


def test_auto_maps_to_no_options():
    assert _resolve_reasoning_options("", "") == {}


def test_thinking_on_maps_to_adaptive():
    assert _resolve_reasoning_options("on", "") == {"thinking": {"type": "adaptive"}}


def test_thinking_off_maps_to_disabled():
    assert _resolve_reasoning_options("off", "") == {"thinking": {"type": "disabled"}}


def test_effort_passes_through():
    for level in ("low", "medium", "high", "max"):
        assert _resolve_reasoning_options("", level) == {"effort": level}


def test_combined_knobs_are_independent():
    assert _resolve_reasoning_options("on", "low") == {
        "thinking": {"type": "adaptive"},
        "effort": "low",
    }


def test_out_of_vocabulary_degrades_to_absent():
    """Defensive: unknown values are dropped with a warning, never raised."""
    assert _resolve_reasoning_options("adaptive", "xhigh") == {}


def test_claude_config_carries_neutral_params_with_auto_defaults():
    cfg = ClaudeConfig()
    assert cfg.thinking == ""
    assert cfg.reasoning_effort == ""
    cfg2 = ClaudeConfig(thinking="on", reasoning_effort="high")
    assert cfg2.thinking == "on"
    assert cfg2.reasoning_effort == "high"
