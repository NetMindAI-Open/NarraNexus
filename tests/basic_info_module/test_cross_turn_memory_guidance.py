"""
@file_name: test_cross_turn_memory_guidance.py
@author: Bin Liang
@date: 2026-04-23
@description: Regression guard for the "Working Memory Across Turns"
section in the BasicInfo system-prompt template.

The Agent runtime persists a turn's reasoning (Agent's `final_output`)
into chat history so the next turn's LLM reads both the reasoning and
the reply. But tool-call arguments and tool-call outputs are still
ephemeral within a single turn — they do NOT survive to the next turn.

This means: when a tool call returns a value the Agent will need later
(a `device_code`, a freshly created `job_id`, a file token, a URL it
minted), the Agent MUST restate that value in its own reasoning text
before the turn ends. Otherwise next-turn-Agent cannot reference it.

This is a cross-module rule (Lark, Job, RAG, any module that yields
machine-readable IDs), so it lives in BasicInfoModule's template where
every trigger source renders it.
"""
from __future__ import annotations


def _prompt_text() -> str:
    from narranexus_plugins.basic_info_module.prompts import (
        BASIC_INFO_MODULE_INSTRUCTIONS,
    )

    return BASIC_INFO_MODULE_INSTRUCTIONS


def test_basic_info_prompt_mentions_reasoning_persists_but_tool_outputs_dont():
    """The template must tell the Agent which slice of its turn survives
    to the next one (its reasoning) and which does not (tool call
    outputs). Without this asymmetry surfaced, the Agent has no reason
    to proactively restate critical values."""
    text = _prompt_text().lower()
    # Reasoning persists
    assert "reasoning" in text, (
        "basic_info template must mention 'reasoning' so the Agent "
        "understands what carries over between turns."
    )
    # Tool outputs ephemeral
    ephemeral_phrasings = [
        "tool output",
        "tool call output",
        "tool-call output",
        "tool results",
        "tool output" in text,  # noqa: E712 — placeholder, see below
    ]
    assert any(p in text for p in ("tool output", "tool call output", "tool-call output", "tool results")), (
        "basic_info template must name the thing that does NOT survive "
        "(tool call outputs / tool results) so the asymmetry is explicit."
    )


def test_basic_info_prompt_tells_agent_to_restate_values_in_reasoning():
    """Surfacing the rule isn't enough — the Agent must be told the
    action: restate the value in its own reasoning before turn end.
    Otherwise it knows the problem but not the remedy."""
    text = _prompt_text().lower()
    signals = [
        "restate",
        "write it in your reasoning",
        "write them in your reasoning",
        "note it in your reasoning",
        "include it in your reasoning",
        "mention it in your reasoning",
    ]
    assert any(s in text for s in signals), (
        "basic_info template must explicitly tell the Agent to restate "
        "tool-output values in its reasoning. Without the action verb "
        "the Agent often misses the point."
    )


def test_basic_info_prompt_gives_concrete_examples_of_values_to_carry():
    """Abstract guidance doesn't land. The template should name
    concrete kinds of values agents lose today (device_code, ids, urls,
    tokens) so the rule is easy to apply."""
    text = _prompt_text().lower()
    # At least two concrete examples expected.
    examples = ["device_code", "job_id", "token", "url", "id", "file path"]
    hits = sum(1 for e in examples if e in text)
    assert hits >= 2, (
        f"basic_info template should name at least 2 concrete value "
        f"types to carry across turns (from {examples}); found {hits} "
        f"matching."
    )
