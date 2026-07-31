"""
@file_name: test_arg_stream.py
@author: Bin Liang
@date: 2026-07-29
@description: Streaming argument extraction: arbitrary fragment splits,
escapes across boundaries, nested shadowing, finalize reconciliation.
"""

import json

import pytest

from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.arg_stream import (
    StreamingArgExtractor,
)


def _stream(raw: str, fields=("content",), chunk: int = 1) -> str:
    """Feed `raw` in `chunk`-sized fragments; return concatenated text
    for the first declared field."""
    ex = StreamingArgExtractor(0, tuple(fields))
    got = []
    for i in range(0, len(raw), chunk):
        for delta in ex.feed(raw[i : i + chunk]):
            if delta.field_path == fields[0]:
                got.append(delta.text)
    return "".join(got)


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 1000])
def test_plain_field_any_split(chunk):
    raw = json.dumps({"content": "hello world", "channel": "chat"})
    assert _stream(raw, chunk=chunk) == "hello world"


@pytest.mark.parametrize("chunk", [1, 2, 5])
def test_escapes_and_unicode_across_boundaries(chunk):
    value = 'line1\nline2 "quoted" \\ 中文 ✅'
    raw = json.dumps({"content": value}, ensure_ascii=True)  # forces \uXXXX
    assert _stream(raw, chunk=chunk) == value


def test_nested_same_name_field_does_not_leak():
    raw = json.dumps(
        {"meta": {"content": "SECRET-NESTED"}, "content": "visible"}
    )
    assert _stream(raw) == "visible"


def test_array_elements_do_not_corrupt_key_tracking():
    raw = json.dumps({"tags": ["content", "x"], "content": "ok"})
    assert _stream(raw) == "ok"


def test_multiple_declared_fields():
    raw = json.dumps({"title": "T", "content": "C"})
    ex = StreamingArgExtractor(3, ("content", "title"))
    seen = {}
    for delta in ex.feed(raw):
        seen.setdefault(delta.field_path, "")
        seen[delta.field_path] += delta.text
        assert delta.call_index == 3
    assert seen == {"title": "T", "content": "C"}


def test_finalize_emits_remainder_after_abort_free_partial_feed():
    raw = json.dumps({"content": "abcdef"})
    ex = StreamingArgExtractor(0, ("content",))
    got = "".join(d.text for d in ex.feed(raw[: len(raw) // 2]))
    rest = "".join(d.text for d in ex.finalize({"content": "abcdef"}))
    assert got + rest == "abcdef"


def test_streamed_equals_final_invariant_fuzz():
    payloads = [
        {"content": ""},
        {"content": "a" * 500},
        {"content": 'esc \\" \\\\ \t end', "other": {"content": "no"}},
        {"x": 1, "content": "后置字段也要流出来"},
    ]
    for payload in payloads:
        raw = json.dumps(payload)
        for chunk in (1, 3, 11):
            ex = StreamingArgExtractor(0, ("content",))
            got = []
            for i in range(0, len(raw), chunk):
                got += [d.text for d in ex.feed(raw[i : i + chunk])]
            got += [d.text for d in ex.finalize(payload)]
            assert "".join(got) == payload["content"]


def test_inactive_without_declared_fields():
    ex = StreamingArgExtractor(0, ())
    assert ex.active is False
    assert ex.feed('{"content":"x"}') == []


def test_abort_stops_emission():
    ex = StreamingArgExtractor(0, ("content",))
    ex.feed('{"content":"he')
    ex.abort()
    assert ex.feed('llo"}') == []
    assert ex.finalize({"content": "hello"}) == []
