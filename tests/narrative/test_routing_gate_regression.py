"""
@file_name: test_routing_gate_regression.py
@date: 2026-07-29
@description: Hold the routing gate to 50 real-world score shapes.

`test_routing_gate.py` pins the gate's LOGIC with synthetic numbers and its own
injected thresholds, so it stays valid across retunes. This file does the other
half: it pins the OUTCOME on shapes actually observed in production, using the
live constants. If someone widens NARRATIVE_MATCH_MARGIN_RATIO back toward the
old behaviour, these fail.

That matters because the failure mode of this gate is silent. The rule it
replaced (`s/(s+1) >= 0.70`, i.e. raw >= 2.33) short-circuited 87.5% of routing
decisions on the same corpus — the LLM arbitration tier was effectively dead
code, and nothing anywhere reported that. A regression here would not raise; it
would just quietly start answering the wrong topic again.

The fixture carries score shapes only — no queries, narrative names, summaries
or ids. See its `_README`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xyz_agent_context.narrative.config import config
from xyz_agent_context.narrative._narrative_impl.routing_gate import evaluate_gate

_FIXTURE = Path(__file__).parent / "fixtures" / "routing_cases.json"


def _load() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _cases():
    return [pytest.param(c, id=c["case"]) for c in _load()["cases"]]


@pytest.mark.parametrize("case", _cases())
def test_real_world_shape_routes_as_intended(case) -> None:
    decision = evaluate_gate(
        case["raw_scores"],
        raw_floor=config.NARRATIVE_MATCH_RAW_FLOOR,
        margin_ratio=config.NARRATIVE_MATCH_MARGIN_RATIO,
    )
    assert decision.short_circuit is case["expect_short_circuit"], (
        f"{case['case']}: scores={case['raw_scores']} "
        f"expected short_circuit={case['expect_short_circuit']}, "
        f"got {decision.short_circuit} ({decision.reason})\n"
        f"WHY THIS SHAPE: {case['note']}"
    )


def test_fixture_policy_matches_live_config() -> None:
    """The fixture's expectations were computed under a specific policy. If the
    live constants move, the expectations must be re-derived deliberately —
    not silently reinterpreted."""
    policy = _load()["policy"]
    assert policy["raw_floor"] == config.NARRATIVE_MATCH_RAW_FLOOR
    assert policy["margin_ratio"] == config.NARRATIVE_MATCH_MARGIN_RATIO


def test_fixture_carries_no_user_content() -> None:
    """Privacy guard: the set is derived from real conversations, so it must
    never regain queries or narrative names on a refresh."""
    blob = _FIXTURE.read_text(encoding="utf-8")
    assert not any("一" <= ch <= "鿿" for ch in blob), (
        "fixture contains CJK text — it should hold score shapes only"
    )
    for case in _load()["cases"]:
        assert set(case) == {"case", "bucket", "raw_scores", "expect_short_circuit", "note"}


def test_fixture_covers_both_outcomes_and_all_buckets() -> None:
    """A set that only ever expects one answer would pass a broken gate."""
    cases = _load()["cases"]
    assert sum(1 for c in cases if c["expect_short_circuit"]) >= 10
    assert sum(1 for c in cases if not c["expect_short_circuit"]) >= 10
    assert {c["bucket"] for c in cases} == {
        "crowded_siblings",
        "decisive_leader",
        "short_query_low_raw",
        "sole_candidate",
        "near_boundary",
    }
