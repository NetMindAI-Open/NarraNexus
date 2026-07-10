"""
@file_name: test_json_extraction.py
@date: 2026-07-08
@description: Tests for _extract_json_from_llm_output's balanced-object fallback.

The shared helper JSON extractor greedily spans first-{ … last-} so a single
NESTED object is captured whole. But the codex CLI helper's stream repeats the
message (streamed increments + an item.completed full copy, sometimes reworded),
producing TWO concatenated objects — the greedy span then fails to parse. The
fallback returns the FIRST balanced object so structured helper calls still work.
"""
import pytest

from xyz_agent_context.agent_framework.openai_agents_sdk import (
    _extract_json_from_llm_output as extract,
)


def test_single_object_unchanged():
    assert extract('{"a": 1}') == '{"a": 1}'


def test_nested_object_captured_whole():
    # greedy path must still win here (non-greedy would truncate)
    assert extract('{"a": {"b": 1}}') == '{"a": {"b": 1}}'


def test_doubled_identical_objects_returns_first():
    assert extract('{"answer":"ok"}{"answer":"ok"}') == '{"answer":"ok"}'


def test_doubled_reworded_objects_returns_first():
    # the realistic codex case: the two copies differ slightly
    assert extract('{"s":"x"}{"s":"y"}') == '{"s":"x"}'


def test_braces_inside_strings_do_not_miscount():
    assert extract('{"a": "}{"}{"junk":1}') == '{"a": "}{"}'


def test_doubled_arrays_returns_first():
    assert extract('[1,2,3][4]') == '[1,2,3]'


def test_markdown_fenced_doubled():
    raw = '```json\n{"k": 1}{"k": 2}\n```'
    assert extract(raw) == '{"k": 1}'


def test_no_json_returns_none():
    assert extract('no json here') is None


def test_mismatched_brackets_rejected():
    # `{` closed by `]` is not balanced (type-checked stack) → no candidate.
    assert extract('{"a": 1]') is None


def test_first_balanced_but_invalid_falls_through_to_next():
    # First balanced object doesn't parse (bareword) → scan the next opener.
    assert extract('{oops} {"k": 2}') == '{"k": 2}'


def test_object_containing_array_captured_whole():
    assert extract('{"xs": [1, 2], "y": 3}') == '{"xs": [1, 2], "y": 3}'
