"""
@file_name: test_claude_reasoning_mapping.py
@author: Bin Liang
@date: 2026-06-10
@description: Claude adapter mapping for the framework-neutral reasoning
params (SlotConfig.thinking / SlotConfig.reasoning_effort).

This maps to what claude-agent-sdk 0.1.43 + the Claude Code CLI 2.1.x
actually accept — NOT 1:1 to the Anthropic API thinking shape. See the
``_resolve_reasoning_options`` docstring for the full chain. Summary
(incident 2026-06-11):

  * The SDK turns ``ClaudeAgentOptions.thinking`` into ``--max-thinking-tokens
    N``; the CLI turns a POSITIVE value into the legacy
    ``thinking:{type:"enabled",budget_tokens:N}`` API shape, which current
    models reject with a 400. The only adaptive lever is ``--effort``.
  * So on/auto/unknown → ``{"effort": <level>}`` (NO "thinking" key → SDK omits
    --max-thinking-tokens → CLI goes adaptive). Auto/unknown effort defaults
    to "high" so --effort is always present (no flags ⇒ enabled ⇒ 400).
  * off → ``{"thinking": {"type": "disabled"}}`` (→ --max-thinking-tokens 0,
    the one value that doesn't 400).

We never produce a positive --max-thinking-tokens, hence never the rejected
``enabled`` shape.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
    _resolve_reasoning_options,
)


def test_auto_maps_to_effort_only_no_thinking():
    """REGRESSION (2026-06-11): auto must emit ONLY effort (default high) and
    NO thinking key. A thinking key → SDK --max-thinking-tokens → CLI sends
    the rejected ``enabled`` shape; no flags at all → same enabled default."""
    out = _resolve_reasoning_options("", "")
    assert out == {"effort": "high"}
    assert "thinking" not in out


def test_thinking_on_maps_to_effort_only():
    out = _resolve_reasoning_options("on", "")
    assert out == {"effort": "high"}
    assert "thinking" not in out


def test_thinking_off_maps_to_disabled_no_effort():
    """off → disabled (→ --max-thinking-tokens 0). No effort with thinking off."""
    assert _resolve_reasoning_options("off", "") == {"thinking": {"type": "disabled"}}


def test_explicit_effort_passes_through_without_thinking():
    for level in ("low", "medium", "high", "max"):
        out = _resolve_reasoning_options("", level)
        assert out == {"effort": level}
        assert "thinking" not in out


def test_on_with_effort_is_effort_only():
    assert _resolve_reasoning_options("on", "low") == {"effort": "low"}


def test_off_ignores_effort():
    """Thinking off wins — effort is moot and must not appear (the CLI would
    otherwise still drive thinking via --effort)."""
    assert _resolve_reasoning_options("off", "high") == {"thinking": {"type": "disabled"}}


def test_unknown_thinking_degrades_to_effort_only():
    """Unknown thinking value → adaptive path (effort only), never absent
    (which resurrects the CLI's rejected enabled default)."""
    out = _resolve_reasoning_options("weird", "")
    assert out == {"effort": "high"}
    assert "thinking" not in out


def test_unknown_effort_defaults_to_high():
    """Unknown effort (e.g. xhigh, which CLI 2.1.x doesn't list) → 'high', so
    --effort is still present and the CLI stays on the adaptive path."""
    assert _resolve_reasoning_options("", "xhigh") == {"effort": "high"}


def test_claude_config_carries_neutral_params_with_auto_defaults():
    cfg = ClaudeConfig()
    assert cfg.thinking == ""
    assert cfg.reasoning_effort == ""
    cfg2 = ClaudeConfig(thinking="on", reasoning_effort="high")
    assert cfg2.thinking == "on"
    assert cfg2.reasoning_effort == "high"
