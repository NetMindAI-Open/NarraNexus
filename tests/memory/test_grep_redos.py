"""
@file_name: test_grep_redos.py
@author:
@date: 2026-08-10
@description: grep_filter's regex path must be ReDoS-safe — the pattern is
agent/LLM-supplied and, once grep is served over HTTP, an unbounded match would
wedge the shared API loop. grep_filter returns (hits, truncated); these pin:
(1) an adversarial pattern returns bounded AND flags truncation; (2) a passed
deadline is honoured; (3) normal regex + substring + invalid-regex fallback work
and report truncated=False.
"""
from __future__ import annotations

import time

from narranexus.platform.memory._memory_impl.retrieval import (
    grep_filter,
    _GREP_PER_MATCH_TIMEOUT_S,
    _GREP_REQUEST_BUDGET_S,
)


class _Rec:
    """Minimal stand-in — grep_filter only reads .content_text."""
    def __init__(self, text):
        self.content_text = text


def test_redos_pattern_is_bounded_and_flags_truncation():
    # (a|aa)+$ on a long non-matching string is catastrophic backtracking. With
    # the per-match timeout it returns quickly (skipping the record) AND reports
    # truncated — so the caller won't present the empty result as "definitely no
    # match". Non-vacuous: stdlib re here would hang.
    recs = [_Rec("a" * 60 + "!")]
    t = time.time()
    hits, truncated = grep_filter(recs, r"(a|aa)+$", regex=True)
    elapsed = time.time() - t
    assert hits == []
    assert truncated is True
    assert elapsed < _GREP_PER_MATCH_TIMEOUT_S + _GREP_REQUEST_BUDGET_S + 1.0


def test_passed_deadline_in_the_past_truncates_immediately():
    # coordinator threads a shared deadline; a deadline already in the past means
    # the budget is spent, so the scan stops before touching any record.
    recs = [_Rec("agent_abc")]
    hits, truncated = grep_filter(recs, r"agent_\w+", regex=True, deadline=time.monotonic() - 1)
    assert hits == [] and truncated is True


def test_normal_regex_matches_and_is_not_truncated():
    recs = [_Rec("order id agent_abc123 shipped"), _Rec("nothing here")]
    hits, truncated = grep_filter(recs, r"agent_\w+", regex=True)
    assert [r.content_text for r in hits] == ["order id agent_abc123 shipped"]
    assert truncated is False


def test_regex_is_case_insensitive_by_default():
    hits, _ = grep_filter([_Rec("HELLO World")], r"hello", regex=True)
    assert len(hits) == 1


def test_invalid_regex_falls_back_to_substring():
    # "(" is an invalid pattern; grep_filter must not raise — treats it as a
    # literal substring, and substring never truncates.
    recs = [_Rec("a literal ( paren"), _Rec("no match")]
    hits, truncated = grep_filter(recs, "(", regex=True)
    assert [r.content_text for r in hits] == ["a literal ( paren"]
    assert truncated is False


def test_substring_mode_unaffected():
    recs = [_Rec("find the ORDER-123 token"), _Rec("unrelated")]
    hits, truncated = grep_filter(recs, "order-123", regex=False)
    assert len(hits) == 1 and truncated is False
