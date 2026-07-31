"""
@file_name: test_fallback_reply_honesty.py
@date: 2026-07-30
@description: The helper-LLM fallback reply must never promise work the turn
did not do.

Incident (2026-07-29, reported by Jiaxi): a user asked an agent to write a Word
document from an image. The agent loop ended after one turn having produced
only intent as its reasoning ("let me try again with image understanding") and
called no tool. The no_reply fallback is instructed to speak "the message the
agent should have sent", so it faithfully voiced that intent — the user was
promised a document that nothing was working on, and the turn was already over.

An unfinished turn is allowed (the platform never force-stops an agent — binding
rule #14, and it never judges the model's choices — #15). What is NOT allowed is
OUR generated text claiming or implying work is under way when the turn has
ended. These tests pin that contract on the prompt the fallback runs with.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _FALLBACK_AFTER_ERROR_INSTRUCTIONS,
    _FALLBACK_NO_REPLY_INSTRUCTIONS,
    _fallback_instructions_for_mode,
)


def test_no_reply_mode_gets_the_no_reply_instructions():
    assert _fallback_instructions_for_mode("no_reply") is _FALLBACK_NO_REPLY_INSTRUCTIONS


def test_after_error_mode_gets_the_after_error_instructions():
    assert (
        _fallback_instructions_for_mode("after_error")
        is _FALLBACK_AFTER_ERROR_INSTRUCTIONS
    )


def test_no_reply_prompt_forbids_promising_unstarted_work():
    """The rule that would have prevented the 2026-07-29 report: the reply may
    not say the agent is doing / about to do something, because the turn is over
    the moment this message is sent."""
    text = _FALLBACK_NO_REPLY_INSTRUCTIONS.lower()
    assert "promise" in text
    # It must state the reason the promise is wrong — that the turn has ended —
    # not just ban a phrasing.
    assert "turn" in text and ("ends" in text or "ended" in text or "over" in text)


def test_no_reply_prompt_requires_honesty_when_nothing_was_produced():
    """When the agent's reasoning holds only intent, the fallback must say the
    work did not happen rather than inventing a completion or a commitment."""
    text = _FALLBACK_NO_REPLY_INSTRUCTIONS.lower()
    assert "intent" in text or "intention" in text
    assert "did not" in text or "didn't" in text or "not done" in text


def test_both_prompts_still_forbid_leaking_internals():
    """The pre-existing contract must survive the honesty additions."""
    for text in (_FALLBACK_NO_REPLY_INSTRUCTIONS, _FALLBACK_AFTER_ERROR_INSTRUCTIONS):
        lowered = text.lower()
        assert "helper_llm" in lowered  # named in the "do NOT mention" rule
        assert "user's language" in lowered
