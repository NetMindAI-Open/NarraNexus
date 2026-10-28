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

`keyword_search` squashes raw BM25 through ``s / (s + 1)`` and the gate
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
from typing import Iterable, List, Optional, Sequence

from ..models import NarrativeSearchResult


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


# ── the second decision: may this turn skip review at all? ──────────────────
#
# The gate above answers "is the BM25 evidence strong?". That question turned
# out to be unanswerable with an absolute number: `bm25_rank` estimates IDF on
# the candidate set, so on prod (26,922 audit rows, 2026-08-14..20, replayed
# byte-exact) ONE query text — `[From Liam] 👊`, 99 turns, same agent — scored
#
#     pool 19 -> 5.66   26 -> 3.35   34 -> 2.41   67 -> 1.09   100 -> 0.02
#
# i.e. RAW_FLOOR=3.0 flips between pool 26 and 34 on identical evidence.
# Gating on the strongest single term instead of the sum does not help: that
# statistic swings 2.89 -> 0.01 across the same range, because it is built from
# the same per-pool IDF. 51.8% of prod decisions are dominated by a term with
# in-pool df = 1, which in a 9-document pool is noise, not rarity.
#
# The RISK, though, is not spread across those numbers. Classifying every prod
# bypass by what it actually did to the conversation:
#
#     staying in the anchored thread   9,229   92.5%
#     switching to another thread        353    3.5%
#     no anchor at all (first turn)      392    3.9%
#
# Thread hijacking can only come from the last two — and B-7 p07 showed what it
# costs: one wrong verdict held 20 of 22 turns in a stranger's thread while the
# updater rewrote that thread's identity until the lexical evidence agreed.
#
# So the necessary condition is structural, not numeric: A BYPASS MAY ONLY KEEP
# A TURN WHERE IT ALREADY WAS. It reads no score, so it cannot drift with pool
# size or query length — of every design measured it is the only one with zero
# cross-pool verdict flips.
#
# Full study: reference/self_notebook/specs/2026-08-20-bm25-gate-redesign-research.md


@dataclass(frozen=True)
class BypassDecision:
    """Whether this turn may skip LLM arbitration, and on what grounds.

    ``reason`` is a stable machine code written to
    ``narrative_routing_audit.bypass_reason``; it is the column that will
    answer "what did the judge decide on the turns Q refused to let through",
    which is the calibration data the next layer needs. ``detail`` is the
    human sentence for the selection log — keep the ids and the numbers in it,
    because a routing complaint has to be diagnosable from one audit row.
    """

    granted: bool
    reason: str
    detail: str


def evaluate_bypass(
    gate: GateDecision,
    *,
    top1_narrative_id: Optional[str],
    anchor_narrative_id: Optional[str],
    is_user_chat: bool,
    has_participant_narratives: bool,
) -> BypassDecision:
    """Decide whether to skip LLM arbitration, given the score gate's verdict.

    Deliberately a SECOND function rather than more parameters on
    ``evaluate_gate``: the score gate is a pure function of numbers with a
    50-shape regression net pinned to it, and the anchor rule is a pure
    function of identity. Keeping them apart means the strength question stays
    independently tunable (that is the whole reason it was extracted in the
    first place) while this rule can be reasoned about without a score in
    sight.

    Order matters — the reason recorded is the FIRST rule that refused, so a
    turn denied for crowded scores is not filed under "switched threads".

    Args:
        gate: the floor/margin verdict. Still has veto: this rule ADDS a
            necessary condition, it does not replace one. Dropping the margin
            would LOOSEN the gate (a lone scoring candidate gets margin=inf by
            construction — the weakest evidence scoring highest), and loosening
            needs the real-pool arm, not a unit test.
        top1_narrative_id: what BM25 wants to route to. ``None`` when nothing
            scored.
        anchor_narrative_id: ``session.current_narrative_id`` — the thread the
            conversation is already in.
        is_user_chat: False for background triggers (cron job, message bus, IM
            webhook). ``narrative_service.select`` deliberately does not
            advance the session anchor on those, so they have no anchor BY
            DESIGN — on prod, 388 of the 392 anchorless bypasses are exactly
            these. They keep the old rule; denying them would push a block of
            correct broad-evidence routings (max-term share p50 0.03) into the
            judge's ``no_topic`` exit, which is a known-unfixed residual dump.
            The reason code is recorded so that arm can be decided from data.
        has_participant_narratives: P0-4 — a BM25 hit on the user's own thread
            must not outrank a task they were invited into.

    Returns:
        BypassDecision. ``granted=False`` routes to ``_llm_unified_match``,
        which is a strictly stronger decider — not a fallback and not an error.
    """
    if has_participant_narratives:
        return BypassDecision(
            granted=False,
            reason="participant_present",
            detail=(
                "user is a PARTICIPANT in another thread; forcing LLM "
                "arbitration (P0-4)"
            ),
        )

    if not top1_narrative_id:
        return BypassDecision(
            granted=False,
            reason="no_candidates",
            detail="no BM25 candidate scored; deferring to LLM arbitration",
        )

    if not gate.short_circuit:
        return BypassDecision(
            granted=False, reason="score_gate", detail=gate.reason
        )

    if not is_user_chat:
        return BypassDecision(
            granted=True,
            reason="background_scope",
            detail=(
                "background trigger has no session anchor by design; "
                f"score gate alone decides ({gate.reason})"
            ),
        )

    if not anchor_narrative_id:
        return BypassDecision(
            granted=False,
            reason="no_anchor",
            detail=(
                "no live thread to stay in, so there is nothing to confirm "
                "without the judge; deferring to LLM arbitration"
            ),
        )

    if top1_narrative_id != anchor_narrative_id:
        return BypassDecision(
            granted=False,
            reason="anchor_miss",
            detail=(
                f"BM25 wants to switch threads (anchor={anchor_narrative_id}, "
                f"top1={top1_narrative_id}); a switch is never taken without "
                f"the judge ({gate.reason})"
            ),
        )

    return BypassDecision(
        granted=True,
        reason="anchor_match",
        detail=(
            f"staying in the anchored thread {anchor_narrative_id}; "
            f"{gate.reason}"
        ),
    )


# ── the same decision, one tier earlier: the zero-LLM shutter ───────────────
#
# Merged routing runs BM25 before anything else, which makes `evaluate_bypass`
# answerable BEFORE the first LLM call rather than after it. That is the whole
# shutter: not a new rule, the existing one relocated.
#
# `anchor_match` is the only verdict that opens it, and the exclusions are the
# point rather than an oversight:
#   * `background_scope` GRANTS a bypass on the two-call path (a background
#     trigger has no session anchor by design), but it grants it on the score
#     gate alone — no identity check — so it is exactly the population the
#     shutter must not release. Those turns pay one merged call instead.
#   * `anchor_miss` is a thread SWITCH, and a switch is never taken without a
#     model. B-7 p07 is what one wrong switch costs: 20 of 22 turns held in a
#     stranger's thread while the updater rewrote that thread's identity until
#     the lexical evidence agreed.
#   * `participant_present` keeps P0-4 structural.
#
# Measured on four replay arms: with the margin condition in place, bypasses
# that CHANGED the outcome ("DIVERTED") were 0/142, 0/186, 0/224 and 0/304.
# Every misconfirmation that did change an outcome sat at margin 1.06-1.27 —
# which is why the shutter reads the gate's verdict and never the raw total.
SHUTTER_REASON = "anchor_match"


def shutter_opens(bypass: BypassDecision) -> bool:
    """May this turn skip the LLM entirely and stay where it already is?

    A predicate over an existing decision on purpose: it must be impossible for
    the shutter and the two-call bypass to drift apart, because the calibration
    behind the shutter's release rate IS the bypass rule's calibration. If this
    ever needs a condition `evaluate_bypass` does not have, add it THERE — with
    a real-pool arm, not a unit test.
    """
    return bypass.granted and bypass.reason == SHUTTER_REASON


def pick_menu(
    ranked: Sequence[NarrativeSearchResult],
    *,
    exclude_ids: Iterable[str],
    limit: int,
) -> List[NarrativeSearchResult]:
    """The menu rows: highest-scoring threads that are neither the anchor nor a
    participant, and that actually scored.

    Three exclusions, three different reasons:
      * the ANCHOR has its own section — rendered twice it reads as two
        candidates, and the asymmetry ("staying is the default, the menu is
        evidence for leaving") collapses into a four-way beauty contest;
      * a PARTICIPANT thread has its own section and its own priority rule;
        letting it also be a keyword row is precisely the fusion P0-4 forbids
        (today's judge does render some of them twice — that is not a shape to
        carry forward);
      * a ZERO score means zero term overlap, so the row carries no evidence for
        switching, which is the only thing the menu is for.
    """
    excluded = set(exclude_ids)
    out: List[NarrativeSearchResult] = []
    for result in sorted(ranked, key=lambda r: r.raw_score, reverse=True):
        if result.narrative_id in excluded or result.raw_score <= 0:
            continue
        out.append(result)
        if len(out) >= limit:
            break
    return out
