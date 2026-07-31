"""
@file_name: test_routing_gate.py
@date: 2026-07-29
@description: The "high confidence, skip the LLM" gate must key off evidence
strength, not a squashed absolute score.

Prod 2026-07-29 (agent_dd505db5ff12, user lustig.zhang@gmail.com): a kendo-armour
question was routed into a Suzhou-industrial-site narrative and the agent
answered the wrong topic for several turns. Cause: `_keyword_search` normalises
BM25 with ``s / (s + 1)`` and the gate compares that to 0.70 — algebraically
``raw >= 2.33``, which a handful of incidental CJK character collisions clears.
Measured against the real prod narratives, 5 of 5 test queries cleared the gate
and skipped LLM arbitration, including "帮我查一下明天上海的天气怎么样", which
is unrelated to every narrative that agent has.

The fix keys the gate off RAW BM25 with two conditions that must BOTH hold:

  1. absolute floor  — top1 must clear a raw score that means real overlap
  2. relative margin — top1 must lead top2 by a ratio

Rationale for the pair: raw BM25 has no cross-corpus meaning (IDF is computed
on a 5-to-12 doc candidate set), so an absolute floor alone is arbitrary. But
WITHIN one query the spread is meaningful — a true hit pulls away, while noise
matches bunch up (the prod five scored 0.970/0.903/0.894/0.781 normalised, i.e.
all crowded together). The floor alone would admit crowded-but-high sets; the
margin alone would admit "both terrible, one twice as terrible".

Not short-circuiting is cheap and safe: the else branch is LLM arbitration,
which already exists and currently runs on only ~4% of turns.
"""

from __future__ import annotations

import pytest

from xyz_agent_context.narrative._narrative_impl.routing_gate import (
    GateDecision,
    evaluate_gate,
)

FLOOR = 8.0
MARGIN = 1.5


def _gate(*raw_scores: float) -> GateDecision:
    return evaluate_gate(list(raw_scores), raw_floor=FLOOR, margin_ratio=MARGIN)


# ---------------- the shape that broke prod --------------------------


def test_crowded_scores_do_not_short_circuit() -> None:
    """Several candidates strong-ish and bunched: no one has earned the turn.

    This is the shape the old rule could not express at all — it only ever
    looked at position 1.
    """
    d = _gate(21.0, 19.5, 18.0)
    assert d.short_circuit is False
    assert "crowded" in d.reason.lower()


def test_reproduces_the_prod_misroute_shape() -> None:
    """A leader with almost no real overlap, field close behind.

    Modelled on the 2026-07-29 07:11 misroute scored against the narrative
    summaries as they stood before the kendo topic was absorbed: raw 1.81 for
    the Suzhou narrative, where the entire overlap with the query was the
    characters 业 and 高.

    This pins the SHAPE, not the live numbers — the production decision scored
    higher because that narrative carried a much longer accumulated summary
    than this reconstruction. FLOOR and MARGIN are chosen from the offline
    eval set; this test injects its own so it stays valid across retunes.
    """
    d = _gate(1.81, 1.50, 1.41, 1.29)
    assert d.short_circuit is False
    assert "floor" in d.reason.lower()


def test_weak_top_score_does_not_short_circuit_even_if_it_leads() -> None:
    """'Both terrible, one twice as terrible' must still reach the LLM."""
    d = _gate(2.4, 1.0)
    assert d.short_circuit is False
    assert "floor" in d.reason.lower()


def test_unrelated_query_shape_does_not_short_circuit() -> None:
    """Reproduces the '明天上海天气' case: raw 3.6 cleared the OLD 0.70 gate."""
    d = _gate(3.6, 2.9, 2.1)
    assert d.short_circuit is False


# ---------------- genuine high confidence still short-circuits -------


def test_decisive_leader_short_circuits() -> None:
    d = _gate(40.0, 4.0, 1.0)
    assert d.short_circuit is True
    assert d.margin == pytest.approx(10.0)


def test_sole_strong_candidate_short_circuits() -> None:
    """One candidate, no competitor: the floor is the only evidence available."""
    d = _gate(25.0)
    assert d.short_circuit is True


def test_sole_weak_candidate_defers_to_llm() -> None:
    """A lone weak match must NOT be accepted just because it is unopposed —
    the LLM tier is also the path that can decide 'none of these, create new'."""
    d = _gate(3.0)
    assert d.short_circuit is False


# ---------------- boundaries and degenerate input --------------------


def test_exactly_at_both_thresholds_short_circuits() -> None:
    d = _gate(FLOOR, FLOOR / MARGIN)
    assert d.short_circuit is True


def test_just_below_margin_defers() -> None:
    d = _gate(FLOOR, FLOOR / (MARGIN - 0.2))
    assert d.short_circuit is False


def test_no_candidates_defers() -> None:
    d = _gate()
    assert d.short_circuit is False
    assert d.top1_raw == 0.0


def test_zero_second_score_is_treated_as_decisive_not_divide_by_zero() -> None:
    d = _gate(20.0, 0.0)
    assert d.short_circuit is True
    assert d.margin == float("inf")


def test_decision_reports_the_numbers_it_used() -> None:
    """The reason string lands in logs; it must carry the evidence."""
    d = _gate(30.0, 2.0)
    assert d.top1_raw == 30.0
    assert d.top2_raw == 2.0
    assert d.margin == pytest.approx(15.0)
    assert d.reason
