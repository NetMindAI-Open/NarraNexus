"""
@file_name: merged_prep.py
@author: NetMind.AI
@date: 2026-08-27
@description: The merged path's BM25 preparation — one scoring pass, the
              anchor's counterfactual standing, and the audit's tier-2 half.

Moved out of retrieval.py on review (2026-08-27, I6): retrieval had crossed
1,400 lines and, worse, imported `merged_router` upward for `pick_menu` —
the layering said "executor depends on decider". `pick_menu` now lives in
`routing_gate` (a pure rule, like the shutter), this module owns the
merged-specific preparation, and retrieval keeps only the executors every
decider shares (`_score_pool`, the loaders, the landings).

Takes the `NarrativeRetrieval` instance as its first argument — a
collaborator, passed by the caller: runtime object, type imported under
TYPE_CHECKING only, no import-time upward dependency.
"""

from __future__ import annotations

import time as _perf
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

from ..models import Narrative, NarrativeSearchResult
from .retrieval import commit_scored_pool
from .routing_gate import BypassDecision, pick_menu, shutter_opens

if TYPE_CHECKING:
    from ..models import RoutingAudit
    from .retrieval import NarrativeRetrieval, ScoredPool


@dataclass(frozen=True)
class MergedRoutingPrep:
    """A scored pool plus where the ANCHOR placed in it.

    Thin wrapper over `ScoredPool` rather than a second scoring pass: merged
    routing needs exactly what the two-tier path and the shadow recorder need,
    plus three numbers about one particular candidate. Wrapping keeps
    `_score_pool` the one definition of "what a BM25 pass produces" — the
    property PR #365 review round 1 was about.

    The anchor's standing is here and not in `ScoredPool` because `ScoredPool`
    deliberately knows nothing about sessions, and "the anchor" is a session
    concept.
    """

    scored: "ScoredPool"
    #: 1-based among candidates that actually scored; None when the anchor
    #: scored nothing (8.2%-49.3% of continuity turns in the replay arms) or
    #: when there is no anchor at all.
    anchor_bm25_rank: Optional[int]
    anchor_raw_score: Optional[float]
    #: Would the anchor have been on the menu WITHOUT the unconditional
    #: injection? False is the case §3.2 exists for.
    anchor_in_menu: Optional[bool]

    @property
    def bypass(self) -> BypassDecision:
        return self.scored.bypass

    @property
    def ranked(self) -> List[NarrativeSearchResult]:
        return self.scored.search_results

    @property
    def participant_narratives(self) -> List[Narrative]:
        return self.scored.participant_narratives

    @property
    def shutter_granted(self) -> bool:
        """May this turn skip the LLM entirely? `evaluate_bypass`'s
        `anchor_match` verdict and nothing else — see routing_gate."""
        return shutter_opens(self.scored.bypass)


async def prepare_merged_routing(
    retrieval: "NarrativeRetrieval",
    query: str,
    user_id: str,
    agent_id: str,
    *,
    anchor_narrative_id: Optional[str],
    is_user_chat: bool,
    audit: "RoutingAudit",
    snapshots: dict,
    menu_size: int,
) -> MergedRoutingPrep:
    """BM25 first, and everything decidable without an LLM answered with it.

    Same scoring pass as the other two callers (`_score_pool`); what differs
    is WHEN, and therefore what the answer can be used for. On the two-call
    path this work happened only after continuity had already said no —
    which is why the shutter's releasable population could never be
    measured, and why slice 0 had to record it separately at all.

    Fills the audit's tier-2 half in place, including ``gate_short_circuit``,
    which keeps its established meaning ("this turn skipped LLM arbitration
    because floor+margin plus identity said so"): the shutter IS that rule
    moved one tier earlier, so giving it a different column would fork one
    fact into two. Binding rule #6 cuts both ways — an existing column must
    not change meaning, and it must not silently stop accumulating either.

    NOT a shadow row: this pool decides. `pool_is_shadow` stays False.
    """
    _t_retrieve = _perf.monotonic()
    scored = await retrieval._score_pool(
        query, user_id, agent_id,
        top_k=menu_size,
        anchor_narrative_id=anchor_narrative_id,
        is_user_chat=is_user_chat,
        # Full-depth ranking is `_score_pool`'s only mode now (round 3, I1):
        # the anchor instrument reads true ranks and both arms record the
        # same precision, while snippets stay bounded to the head.
    )
    elapsed_ms = int((_perf.monotonic() - _t_retrieve) * 1000)

    # Where the anchor placed among candidates that ACTUALLY SCORED, in
    # BM25 order. Not the position in `search_results`: the participant
    # merge re-sorts that list on a synthetic 0.5 similarity, so a
    # participant thread can sit above a real keyword hit there — ranking
    # the anchor against that would be ranking it against noise.
    scoring = sorted(
        (r for r in scored.search_results if r.raw_score > 0),
        key=lambda r: r.raw_score,
        reverse=True,
    )
    anchor_rank: Optional[int] = None
    anchor_score: Optional[float] = None
    anchor_in_menu: Optional[bool] = None
    if anchor_narrative_id:
        anchor_score = 0.0
        # The counterfactual must apply the REAL menu rule (review round
        # minor 6): `pick_menu` excludes participant threads, and a
        # participant that also scored would otherwise occupy a
        # `scoring[:menu_size]` slot and squeeze the anchor out — reading
        # as "the anchor needed the injection" when it did not. The anchor
        # itself is NOT excluded: whether it makes the menu is the question —
        # including when the anchor is ALSO a participant thread (the turn
        # after a participant landing; round 9, I1 — unfixed, this instrument
        # recorded False for that whole population even when the anchor
        # topped BM25).
        counterfactual_menu = pick_menu(
            scored.search_results,
            exclude_ids=(
                {n.id for n in scored.participant_narratives}
                - {anchor_narrative_id}
            ),
            limit=menu_size,
        )
        anchor_in_menu = anchor_narrative_id in [
            r.narrative_id for r in counterfactual_menu
        ]
        for position, result in enumerate(scoring, start=1):
            if result.narrative_id == anchor_narrative_id:
                anchor_rank = position
                anchor_score = result.raw_score
                break

    # ── commit block: pure assignment, cannot raise ──────────────────
    commit_scored_pool(
        audit, snapshots, scored,
        retrieve_ms=elapsed_ms,
        gate_short_circuit=shutter_opens(scored.bypass),
    )
    # The §3.2 anchor instrument is merged-arm-only — not part of the shared
    # commit block on purpose.
    audit.anchor_bm25_rank = anchor_rank
    audit.anchor_raw_score = anchor_score
    audit.anchor_in_menu = anchor_in_menu

    return MergedRoutingPrep(
        scored=scored,
        anchor_bm25_rank=anchor_rank,
        anchor_raw_score=anchor_score,
        anchor_in_menu=anchor_in_menu,
    )
