"""
@file_name: routing_gate.py
@date: 2026-07-29
@description: Decide whether BM25 evidence is strong enough to route a turn
without asking the LLM.

Split out of `retrieval.retrieve_top_k` so the decision is a pure function over
numbers: it is the one piece of narrative routing that must be tunable against
an offline eval set, and it was previously three inline conditions tangled with
DB loads and candidate assembly.

WHY THE OLD RULE FAILED (prod 2026-07-29, agent_dd505db5ff12)

`_keyword_search` squashes raw BM25 through ``s / (s + 1)`` and the gate
compared that to 0.70. That is algebraically ``raw >= 2.33`` — and under the
per-character CJK tokenizer (Chinese has no spaces, so `tokenize` emits
unigrams) a handful of incidental character collisions clears it. `工业` and
`武道具` share 业; `高铁新城` and `高井武道具` share 高. Measured against that
agent's real narratives, 5 of 5 queries cleared the gate and skipped LLM
arbitration — including "帮我查一下明天上海的天气怎么样", which relates to
none of its narratives. Routing was effectively random but confident, and the
one layer that could have caught it was skipped precisely when the bogus score
was highest.

WHY TWO CONDITIONS, NOT ONE

Raw BM25 carries no cross-corpus meaning here: `bm25_rank` computes IDF on the
candidate set itself, which is 5-12 narratives, so the same overlap yields
different numbers for different agents. An absolute floor alone is therefore
arbitrary.

Within a single query, though, the SPREAD is meaningful — both candidates saw
the same IDF table. A true hit pulls away from the field; noise matches bunch
up. The prod five normalised to 0.970 / 0.903 / 0.894 / 0.781: all crowded.

So both must hold:
  - floor  kills "everything is weak, pick the least weak"
  - margin kills "everything is strong-ish, pick arbitrarily"

Failing the gate is not an error and not a fallback — it routes to
`_llm_unified_match`, which already exists, is the only semantic check in the
pipeline, and currently runs on ~4% of turns. Sending more traffic there is the
point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class GateDecision:
    """Outcome of the high-confidence check, with the evidence behind it.

    ``reason`` is written into the selection log; keep it specific enough that
    a routing complaint can be diagnosed from logs alone.
    """

    short_circuit: bool
    reason: str
    top1_raw: float
    top2_raw: float
    margin: float


def evaluate_gate(
    raw_scores: Sequence[float],
    *,
    raw_floor: float,
    margin_ratio: float,
) -> GateDecision:
    """Decide whether to accept the BM25 ranking without LLM arbitration.

    Args:
        raw_scores: RAW BM25 scores, any order. Must be the un-normalised
            values — ``s / (s + 1)`` destroys the spread this depends on.
        raw_floor: Minimum raw score for the leader.
        margin_ratio: Required ``top1 / top2``.

    Returns:
        GateDecision. ``short_circuit=False`` means "defer to the LLM tier",
        which is a normal, cheap outcome — not a failure.
    """
    ordered = sorted((s for s in raw_scores), reverse=True)

    if not ordered:
        return GateDecision(
            short_circuit=False,
            reason="no BM25 candidates; deferring to LLM arbitration",
            top1_raw=0.0,
            top2_raw=0.0,
            margin=0.0,
        )

    top1 = ordered[0]
    top2 = ordered[1] if len(ordered) > 1 else 0.0
    # A lone candidate has nothing to be measured against, so the margin is
    # unbounded by construction — the floor is then the only real evidence.
    margin = float("inf") if top2 <= 0 else top1 / top2

    if top1 < raw_floor:
        return GateDecision(
            short_circuit=False,
            reason=(
                f"top1 raw={top1:.2f} below floor={raw_floor:.2f}; "
                f"weak overlap, deferring to LLM arbitration"
            ),
            top1_raw=top1,
            top2_raw=top2,
            margin=margin,
        )

    if margin < margin_ratio:
        return GateDecision(
            short_circuit=False,
            reason=(
                f"top1 raw={top1:.2f} vs top2 raw={top2:.2f} "
                f"(margin={margin:.2f} < {margin_ratio:.2f}); candidates are "
                f"crowded, deferring to LLM arbitration"
            ),
            top1_raw=top1,
            top2_raw=top2,
            margin=margin,
        )

    return GateDecision(
        short_circuit=True,
        reason=(
            f"top1 raw={top1:.2f} clears floor={raw_floor:.2f} and leads "
            f"top2 raw={top2:.2f} by margin={margin:.2f}"
        ),
        top1_raw=top1,
        top2_raw=top2,
        margin=margin,
    )
