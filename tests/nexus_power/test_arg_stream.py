"""
@file_name: test_arg_stream.py
@author: Bin Liang
@date: 2026-07-29
@description: Streaming argument extraction: arbitrary fragment splits,
escapes across boundaries, nested shadowing, finalize reconciliation.
"""

import json

import pytest

from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.arg_stream import (
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


# -- astral chars (emoji) from ASCII-escaping providers ----------------
# MiniMax streams tool arguments ensure_ascii-style, so every emoji arrives
# as a UTF-16 surrogate-pair escape (``👋``). Decoding each
# escape alone produced two lone surrogates; the NDJSON event log then
# raised "'utf-8' codec can't encode ... surrogates not allowed" and the
# NarraMessenger turn died without sending its reply (prod 2026-09-11).


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 6, 7, 13, 1000])
def test_emoji_surrogate_pair_joins_across_any_split(chunk):
    value = "👋 你好，我是知识导航 🚀🎉 done"
    raw = json.dumps({"content": value}, ensure_ascii=True)
    assert "\\ud83d" in raw  # the provider shape under test
    got = _stream(raw, chunk=chunk)
    assert got == value
    got.encode("utf-8")  # strictly encodable, never a lone surrogate


def test_unpaired_surrogates_become_replacement_char():
    raw = '{"content": "a\\ud83d b \\udc4b c \\ud83d\\ud83d\\udc4b \\ud83d"}'
    got = _stream(raw)
    assert got == "a� b � c �👋 �"
    got.encode("utf-8")


def test_streamed_equals_scrubbed_final_with_emoji():
    payload = {"content": "前缀 🙂 后缀"}
    raw = json.dumps(payload, ensure_ascii=True)
    for cut in range(len(raw) + 1):
        ex = StreamingArgExtractor(0, ("content",))
        got = "".join(d.text for d in ex.feed(raw[:cut]))
        got += "".join(d.text for d in ex.finalize(json.loads(raw)))
        assert got == payload["content"], cut


def test_scrub_surrogates():
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.arg_stream import (
        scrub_surrogates,
    )

    assert scrub_surrogates("plain 中文 👋") == "plain 中文 👋"
    assert scrub_surrogates("👋") == "👋"
    assert scrub_surrogates("x\ud83dy\udc4bz") == "x�y�z"


def test_finalize_reconciles_against_scrubbed_final_with_lone_halves():
    # A provider sending an unpaired half: the complete args (json.loads)
    # still carry the lone surrogate; finalize must never re-emit it.
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.arg_stream import (
        scrub_surrogates,
    )

    raw = '{"content": "a\\ud83d b \\udc4b c \\ud83d\\ude80 end"}'
    final = json.loads(raw)
    assert "\ud83d" in final["content"]  # json.loads keeps the lone half
    expected = scrub_surrogates(final["content"])
    for cut in range(len(raw) + 1):
        ex = StreamingArgExtractor(0, ("content",))
        got = "".join(d.text for d in ex.feed(raw[:cut]))
        got += "".join(d.text for d in ex.finalize(final))
        assert got == expected, cut
        got.encode("utf-8")


def test_parse_args_scrubs_lone_surrogates_everywhere():
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.model_client import (
        _parse_args,
    )

    args, err, truncated = _parse_args(
        '{"text": "hi \\ud83d\\udc4b \\ud83d", "nested": {"k\\udc4b": ["x\\ud83d"]}, "n": 1}'
    )
    assert err is None and truncated is False
    assert args == {"text": "hi 👋 �", "nested": {"k�": ["x�"]}, "n": 1}
    json.dumps(args, ensure_ascii=False).encode("utf-8")
