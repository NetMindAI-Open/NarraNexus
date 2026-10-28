"""
Narrative retrieval implementation

@file_name: retrieval.py
@author: NetMind.AI
@date: 2025-12-22
@description: BM25 keyword retrieval + LLM unified match for narrative routing.
"""

from __future__ import annotations

import asyncio
import time as _perf
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from loguru import logger

from narranexus.platform.agent_framework.llm.call_tagging import tag_last_llm_call

from ..config import config
from ..models import (
    Narrative,
    NarrativeSearchResult,
    NarrativeSelectionResult,
    NarrativeType,
    RoutingAudit,
    RoutingCandidate,
)
from .crud import NarrativeCRUD
from .routing_gate import (
    BypassDecision,
    GateDecision,
    evaluate_bypass,
    evaluate_gate,
)
from .landings import (
    candidate_labels,
    assemble_match_landing,
    load_participant_landing,
)
from .default_narratives import (
    DEFAULT_NARRATIVES_CONFIG,
    ensure_default_narratives,
    build_default_narrative_id_pattern,
)
from narranexus.platform.utils.logging import timed

# Use common utilities from utils
from narranexus.platform.utils.text import (
    extract_keywords,
    strip_routing_prefix,
    truncate_text,
)
from narranexus.platform.utils.db.db_factory import get_db_client
from ._retrieval_llm import llm_judge_unified

if TYPE_CHECKING:
    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.repository import NarrativeRepository

# How much of a candidate reaches the judge. The judge prompt is rebuilt from
# scratch on every crowded turn, so each of these is paid per candidate per
# judged turn — hence bounded here rather than "whatever fits".
MAX_MATCHED_TERMS = 5  # Terms shown per candidate, highest contribution first


@dataclass(frozen=True)
class ScoredPool:
    """Everything one BM25 scoring pass produces — and nothing about what to do
    with it.

    Deliberately inert: no audit, no session, no decision. The deciding path
    and the recording path differ only in which of these fields they copy into
    the audit row afterwards, which is the property that makes the two
    populations comparable at all. In-process value object, so a dataclass
    rather than a pydantic model — it never crosses a wire.
    """

    search_results: List[NarrativeSearchResult]
    participant_narratives: List[Narrative]
    gate: GateDecision
    bypass: BypassDecision
    candidates: List[RoutingCandidate]
    snapshots: Dict[str, str]
    keyword_ms: int
    best_score: Optional[float]
    all_scores: Dict[str, float]


def commit_scored_pool(
    audit: "RoutingAudit",
    snapshots: Dict[str, str],
    scored: "ScoredPool",
    *,
    retrieve_ms: Optional[int],
    is_shadow: bool = False,
    gate_short_circuit: Optional[bool] = None,
) -> None:
    """Stamp one ScoredPool onto one audit row — the ONE commit block.

    Three call sites (two-call decider, shadow recorder, merged prep) had
    grown three near-identical hand copies of these assignments (review
    round 9, I2) — the exact drift `_score_pool`'s own docstring records
    paying for once already ("a list that must be kept in sync is a list
    that drifts"). The next ScoredPool-derived column gets written HERE or
    it silently stays NULL on one arm, which reads as "this tier did not
    run".

    Pure assignment, cannot raise — callers rely on that for their
    all-or-nothing recording.

    The three legitimate differences stay with the callers:
      * `retrieve_ms` — its DEFINITION differs per arm (two-call nests the
        judge and stamps it later, so it passes None here; shadow and merged
        are their own single segment);
      * `is_shadow` — only the recorder's rows are shadow;
      * `gate_short_circuit` — THREE-state on purpose: the two-call arm
        writes the bypass verdict, the shadow arm must NOT write it at all
        (rule #6 — the gate decided nothing there), the merged arm writes
        the shutter. None means "leave the column untouched", never "write
        False".
    """
    audit.candidates.extend(scored.candidates)
    snapshots.update(scored.snapshots)
    if is_shadow:
        audit.pool_is_shadow = True
    audit.keyword_ms = scored.keyword_ms
    if retrieve_ms is not None:
        audit.retrieve_ms = retrieve_ms
    audit.gate_top1_raw = scored.gate.top1_raw
    audit.gate_top2_raw = scored.gate.top2_raw
    # inf is not JSON/DOUBLE-safe; a lone candidate has an unbounded margin
    audit.gate_margin = (
        scored.gate.margin if scored.gate.margin != float("inf") else None
    )
    audit.bypass_score_gate = scored.gate.short_circuit
    audit.bypass_reason = scored.bypass.reason
    audit.gate_reason = scored.bypass.detail
    if gate_short_circuit is not None:
        audit.gate_short_circuit = gate_short_circuit


class NarrativeRetrieval:
    """
    Narrative Retrieval

    Responsibilities:
    - BM25 keyword search over the agent's narratives
    - LLM unified-match confirmation / new-narrative creation
    """

    def __init__(self, agent_id: str):
        """
        Initialize retrieval engine

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._crud = NarrativeCRUD(agent_id)
        self._event_service = None  # Dependency injection

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._crud.set_database_client(db_client)

    def set_event_service(self, event_service):
        """Inject EventService"""
        self._event_service = event_service

    async def retrieve_top_k(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        top_k: int,
        narrative_type: NarrativeType = NarrativeType.CHAT,
        *,
        anchor_narrative_id: Optional[str] = None,
        is_user_chat: bool = True,
    ) -> NarrativeSelectionResult:
        """Retrieve Top-K Narratives, and record the evidence behind the choice.

        Thin wrapper over ``_retrieve_top_k``: it owns the RoutingAudit so the
        outcome is stamped in ONE place. The inner method has seven return
        points across itself and ``_llm_unified_match``; filling the audit at
        each of them is exactly the kind of bookkeeping that rots — one new
        branch later and that path silently stops being observable, which is
        the failure mode this whole table exists to end.

        The audit rides on the returned NarrativeSelectionResult;
        ``NarrativeService.select`` adds the continuity half and writes it.
        """
        audit = RoutingAudit(
            agent_id=agent_id, user_id=user_id, query_text=query,
        )
        snapshots: dict = {}
        result = await self._retrieve_top_k(
            query, user_id, agent_id, top_k, narrative_type, audit, snapshots,
            anchor_narrative_id=anchor_narrative_id,
            is_user_chat=is_user_chat,
        )
        audit.selection_method = result.selection_method
        audit.retrieval_method = result.retrieval_method
        audit.chosen_narrative_id = result.narratives[0].id if result.narratives else None
        audit.is_new = result.is_new
        result.audit = audit
        result.audit_snapshots = snapshots
        return result

    async def _retrieve_top_k(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        top_k: int,
        narrative_type: NarrativeType,
        audit: "RoutingAudit",
        snapshots: dict,
        *,
        anchor_narrative_id: Optional[str],
        is_user_chat: bool,
    ) -> NarrativeSelectionResult:
        """
        Retrieve Top-K Narratives (two-tier threshold + LLM unified judgment)

        Workflow:
        0. Ensure default Narratives exist
        1. BM25 keyword search over the agent's narratives (name + summary +
           topic_keywords); add PARTICIPANT narratives at a neutral score
        2. Two-tier threshold judgment:
           a) High confidence (>= high threshold) -> Return Top-K directly
           b) Low confidence (< high threshold) -> LLM unified judgment (search results + default Narratives)
              - Match default type -> Return 1 default Narrative
              - Match search result -> Return Top-K list
              - No match -> Create new Narrative

        Args:
            query: User query
            user_id: User ID
            agent_id: Agent ID
            top_k: Number of results to return
            narrative_type: Narrative type

        Returns:
            NarrativeSelectionResult: Contains Narrative list, selection reason, and other complete info
        """
        logger.info(f"Retrieving Top-{top_k} Narratives: query='{query[:50]}...'")

        # Step 0: Ensure default Narratives exist.
        # Must stay AHEAD of the pool load below and cannot join the gather:
        # it CREATES default narratives when they are missing, and a pool read
        # racing that creation would silently omit them from the BM25 candidate
        # set — a wrong answer, not a slow one.
        with timed("narrative.retrieve.ensure_defaults"):
            await self._ensure_default_narratives(agent_id, user_id)

        # Step 0.5 (P0-4): Narratives where the user is a PARTICIPANT, and
        # Step 1's candidate pool. Two independent reads — neither feeds the
        # other — so they overlap.
        #
        # Honest accounting: measured on a live instance these are ~3ms and
        # ~5ms, against a setup phase whose p50 is 8.5 SECONDS. This saves
        # single-digit milliseconds and is emphatically NOT the fix for
        # narrative-selection latency. The cost is the two helper-LLM round
        # trips either side of it (continuity ~3.9s, unified judge ~4.7s), and
        # anyone arriving here to make selection faster should go there — see
        # the `continuity_ms` / `judge_ms` columns on narrative_routing_audit,
        # which exist to make that obvious without re-deriving it.
        scored = await self._score_pool(
            query, user_id, agent_id,
            top_k=top_k,
            anchor_narrative_id=anchor_narrative_id,
            is_user_chat=is_user_chat,
        )
        search_results = scored.search_results
        participant_narratives = scored.participant_narratives
        has_participant_narratives = len(participant_narratives) > 0
        if has_participant_narratives:
            logger.info(f"P0-4: User is a PARTICIPANT in {len(participant_narratives)} Narratives")
        # Pool read + ranking, and nothing else — the participant query it runs
        # alongside is a different question and must not be charged here. This
        # column answers "is BM25 ever the problem?"; mixing in an unrelated
        # read is how it would answer wrongly.
        # retrieve_ms=None: this arm's retrieve_ms NESTS the judge and is
        # stamped after it, not here. gate_short_circuit carries the bypass
        # verdict — its original "skipped the judge" meaning.
        commit_scored_pool(
            audit, snapshots, scored,
            retrieve_ms=None,
            gate_short_circuit=scored.bypass.granted,
        )
        retrieval_method = "keyword"
        logger.info(f"[NarrativeSelect] Keyword(BM25) search returned {len(search_results)} candidates")

        # Step 2: Two-tier threshold judgment
        best_score = scored.best_score
        all_scores = scored.all_scores

        # First tier: high confidence - return Top-K directly.
        # The gate reads RAW BM25, not the squashed similarity — see
        # routing_gate.evaluate_gate for why. Participant narratives still
        # force LLM judgment regardless: they carry a synthetic neutral score,
        # and a high BM25 hit on the user's OWN narrative should not win over
        # the task they were invited into (P0-4). Both verdicts come from
        # `_score_pool`, the same pass the shadow recorder uses.
        gate, bypass = scored.gate, scored.bypass
        # Gate/bypass columns were stamped by commit_scored_pool above;
        # `bypass_score_gate` preserves the floor/margin series for the next
        # calibration round, `gate_short_circuit` carries the bypass verdict.
        if bypass.granted:
            logger.info(f"[NarrativeSelect] high confidence — {bypass.detail}")
            narratives = []
            for result in search_results[:top_k]:
                narrative = await self._crud.load_by_id(result.narrative_id)
                if narrative:
                    narratives.append(narrative)

            return NarrativeSelectionResult(
                narratives=narratives,
                selection_reason=f"High confidence match: {bypass.detail}",
                selection_method="high_confidence",
                is_new=False,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
                # evermemos_memories removed — EverMemOS decoupled from narrative selection
            )

        if search_results:
            logger.info(
                f"[NarrativeSelect] deferring to LLM ({bypass.reason}) — "
                f"{bypass.detail}"
            )

        # P0-4: If user has PARTICIPANT Narratives, force LLM judgment
        if has_participant_narratives:
            logger.info(f"User has PARTICIPANT Narratives, forcing LLM judgment (best_score={f'{best_score:.2f}' if best_score else 'N/A'})")

        # Second tier: Low confidence - LLM unified judgment
        logger.info(f"Low confidence (score={best_score if best_score else 'N/A'}), using LLM unified judgment...")

        if config.NARRATIVE_MATCH_USE_LLM:
            # Call unified LLM judgment (considers search results, default Narratives, and PARTICIPANT Narratives)
            # This is the slow path — wrap in timed() so the dual cost
            # (LLM call + extra DB loads inside _llm_unified_match) is
            # visible separately from the BM25 keyword search above.
            _t_judge = _perf.monotonic()
            with timed("narrative.retrieve.llm_unified_match") as t:
                result = await self._llm_unified_match(
                    query=query,
                    search_results=search_results[:3] if search_results else [],
                    agent_id=agent_id,
                    user_id=user_id,
                    top_k=top_k,
                    narrative_type=narrative_type,
                    best_score=best_score,
                    participant_narratives=participant_narratives,  # P0-4: Pass PARTICIPANT Narratives
                    retrieval_method=retrieval_method,  # Pass retrieval method
                    audit=audit,
                )
                # Tag with the model + structured-output mode the SDK ended
                # up using inside _llm_unified_match (post-call by contract —
                # see call_tagging's docstring).
                tag_last_llm_call(t)
                # Set before returning, not after: this is the only exit from
                # the judged path, and `retrieve_top_k` stamps the outcome onto
                # the same audit object afterwards.
                audit.judge_ms = int((_perf.monotonic() - _t_judge) * 1000)
                return result

        # LLM not enabled - Create new Narrative directly
        else:
            logger.info("LLM not enabled, creating new topic directly")
            new_narrative = await self.create_from_query(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                narrative_type=narrative_type
            )

            return NarrativeSelectionResult(
                narratives=[new_narrative],
                selection_reason="LLM not enabled, created new topic directly",
                selection_method="new_created",
                is_new=True,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
                # evermemos_memories removed — EverMemOS decoupled from narrative selection
            )

    async def _ensure_default_narratives(self, agent_id: str, user_id: str) -> None:
        """
        Ensure default Narratives exist for the agent-user combination

        Uses NarrativeRepository.count_default_narratives() method for checking,
        avoiding direct SQL in business logic.

        Check logic:
        1. Use Repository to query default Narrative count
        2. If exists, return directly (already initialized)
        3. If not exists, call ensure_default_narratives to create

        Args:
            agent_id: Agent ID
            user_id: User ID
        """
        # C-1: buckets are no longer routing containers, so a new (agent,user)
        # pair must not acquire eight of them. This is the SEEDING half of the
        # change; existing rows are left untouched (binding rule #6) and simply
        # stop being loaded into the pool above.
        if not config.NARRATIVE_DEFAULT_BUCKETS_ENABLED:
            logger.debug(
                "Default buckets disabled — skipping seeding for "
                f"agent {agent_id} + user {user_id}"
            )
            return

        # Use Repository to check if default Narratives already exist (lazy import to avoid circular dependency)
        from narranexus.platform.repository import NarrativeRepository
        db_client = await get_db_client()
        repo = NarrativeRepository(db_client)

        count = await repo.count_default_narratives(agent_id, user_id)

        if count > 0:
            # Default Narratives already exist
            logger.debug(
                f"Default Narratives for Agent {agent_id} + User {user_id} already exist "
                f"({count} found)"
            )
            return

        # Do not exist, need to create
        logger.info(
            f"Default Narratives for Agent {agent_id} + User {user_id} do not exist, creating..."
        )

        try:
            default_narratives = await ensure_default_narratives(
                agent_id=agent_id,
                user_id=user_id,
                crud=self._crud  # Pass crud instance to avoid circular dependency
            )

            logger.info(
                f"Successfully created {len(default_narratives)} default Narratives "
                f"for Agent {agent_id} + User {user_id}"
            )
        except Exception as e:
            logger.exception(
                f"Failed to create default Narratives (agent={agent_id}, user={user_id}): {e}"
            )
            # Do not raise exception, allow continued execution (default Narrative creation failure should not block main flow)

    @classmethod
    def _build_pool_record(
        cls,
        pool: List[Tuple[str, str, bool]],
        search_results: List[NarrativeSearchResult],
        participant_narratives: Optional[List[Narrative]] = None,
    ) -> Tuple[List[RoutingCandidate], Dict[str, str]]:
        """Build the candidate set and its snapshots — a PURE function.

        Returns rather than mutates so a caller can make the whole recording
        atomic. The previous version appended straight into `audit.candidates`
        and the caller's `snapshots` dict, which meant a failure part-way
        through left a half-written row AND orphan rows in
        `narrative_text_snapshots` that no audit row referenced. The shadow
        recorder answered that with a hand-copied list of "which fields to
        reset", which was already missing `gate_reason` in the commit that
        introduced it. A list that must be kept in sync is a list that drifts;
        returning the work instead removes the list.

        Every BM25 pool member is recorded, including the ones that scored
        zero — they still shaped the ranking through IDF and avgdl, so a
        replay that omits them reproduces different numbers.

        Participant narratives are recorded too but are NOT part of the BM25
        pool: they are appended after ranking with a synthetic neutral score
        and never went through bm25_rank. `raw_score` stays 0.0 for them,
        matching what the gate sees (NarrativeSearchResult.raw_score default),
        so a replay does not mistake them for keyword hits.
        """
        from narranexus.platform.repository.narrative_routing_audit_repository import (
            text_hash,
        )

        candidates: List[RoutingCandidate] = []
        snapshots: Dict[str, str] = {}
        scored = {r.narrative_id: r.raw_score for r in search_results}
        participants = {n.id: n for n in (participant_narratives or [])}
        seen: set = set()

        for nid, text, is_default in pool:
            h = text_hash(text)
            snapshots[h] = text
            seen.add(nid)
            candidates.append(RoutingCandidate(
                narrative_id=nid,
                text_hash=h,
                raw_score=scored.get(nid, 0.0),
                is_default=is_default,
                is_participant=nid in participants,
            ))

        for nid, narrative in participants.items():
            if nid in seen:
                continue
            text = narrative.searchable_text()
            h = text_hash(text)
            snapshots[h] = text
            candidates.append(RoutingCandidate(
                narrative_id=nid,
                text_hash=h,
                raw_score=0.0,
                is_default=narrative.is_special == "default",
                is_participant=True,
            ))

        return candidates, snapshots

    async def _score_pool(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        *,
        top_k: int,
        anchor_narrative_id: Optional[str],
        is_user_chat: bool,
    ) -> "ScoredPool":
        """One BM25 scoring pass — the ONE definition, shared by both callers.

        `retrieve_top_k` (which decides) and `record_pool_only` (which only
        records) used to be two hand-written copies of this sequence, and the
        copies had already drifted three ways in the commit that created them:
        a scoring slice of 3 against 6, a `keyword_ms` that excluded the pool
        read on one side, and a bucket precondition that only held on one.
        Slice 0's entire value is that the two populations are comparable, and
        a promise of comparability kept by hand is not kept.

        Returns everything a caller might need and writes NOTHING — no audit,
        no snapshots dict, no session. That is what lets each caller commit the
        result in a single block that cannot fail half-way (see
        `_build_pool_record`), and it is why the participant merge lives HERE
        rather than in the callers: the record must be built AFTER the merge or
        `is_participant` is permanently false for every candidate the P0-4
        rule is about, and a constraint that lives in one function cannot be
        violated by only one of two call sites.

        The ranking is kept at FULL depth for every caller (round 3, I1):
        both arms' `candidates_json` must carry the same precision, and the
        merged path's anchor instrument cannot tell "ranked below the cut"
        from "scored nothing" on a truncated slice. Snippets — prompt
        evidence, nothing else — are computed only for the head
        (`snippet_depth` below), which is where the cost lived.

        `_ensure_default_narratives` is deliberately NOT part of this: it
        CREATES rows, and the recording path must not write business data to
        observe. The merged path decides yet never seeds either — safe
        because the boot gate guarantees buckets are OFF whenever merged is
        ON, and seeding is a no-op with buckets off (two distant facts, held
        together by config._reject_untested_flag_combination; round 7, M6).

        One consequence, stated conditionally because it depends on a flag:
        under `NARRATIVE_DEFAULT_BUCKETS_ENABLED=0` (the shipping value)
        buckets never enter the pool, so the recorded pool IS the pool the
        deciding path would have scored. If that flag is turned back on, a
        shadow pool for an (agent,user) that has never been seeded is missing
        the eight buckets the deciding path would have created and counted into
        IDF/avgdl. The window is narrow — a continuity turn means the previous
        turn already ran the seeding — but it is not empty: flipping the flag
        between two turns hits it.
        """
        async def _load_pool_timed():
            _t0 = _perf.monotonic()
            result = await self.load_pool(agent_id, user_id)
            return result, int((_perf.monotonic() - _t0) * 1000)

        with timed("narrative.retrieve.independent_reads"):
            # Coroutines straight into gather — no `create_task` wrapper.
            # gather already schedules them concurrently, and holding our own
            # Task handles would only add two objects for nothing.
            #
            # `return_exceptions` stays at its default False: either read
            # failing means the candidate set is INCOMPLETE, and routing
            # confidently on the remainder would be a wrong answer dressed as
            # a right one. The caller must see it. (Note: gather does NOT
            # cancel the sibling on first exception — the surviving read runs
            # to completion with nobody to receive it. Harmless here, one
            # wasted DB round trip.)
            participant_narratives, (pool, _pool_ms) = await asyncio.gather(
                self._get_participant_narratives(user_id=user_id, agent_id=agent_id),
                _load_pool_timed(),
            )

        with timed("narrative.retrieve.keyword_search"):
            # rank_pool rather than keyword_search: the audit needs the WHOLE
            # pool with the exact text that was scored, and BM25's IDF/avgdl
            # are computed over that set, so a top-K slice cannot be replayed.
            # keyword_search stays the public seam for select_fast.
            _t_rank = _perf.monotonic()
            search_results = self.rank_pool_full(
                query, pool,
                snippet_depth=max(top_k * 2, config.NARRATIVE_SEARCH_TOP_K),
            )
            _rank_ms = int((_perf.monotonic() - _t_rank) * 1000)

        # P0-4: PARTICIPANT narratives join the candidate list at a synthetic
        # neutral score — keyword search cannot return them, they belong to
        # other users' threads.
        existing_narrative_ids = {r.narrative_id for r in search_results}
        for narrative in participant_narratives:
            if narrative.id not in existing_narrative_ids:
                search_results.append(NarrativeSearchResult(
                    narrative_id=narrative.id,
                    similarity_score=0.5,
                    rank=999
                ))
                logger.info(f"  Added PARTICIPANT Narrative: {narrative.id} (neutral score 0.5)")

        search_results.sort(key=lambda x: x.similarity_score, reverse=True)
        for i, result in enumerate(search_results):
            result.rank = i + 1

        # AFTER the merge, never before — see the class docstring above.
        candidates, snapshots = self._build_pool_record(
            pool, search_results, participant_narratives
        )

        gate = evaluate_gate(
            [r.raw_score for r in search_results],
            raw_floor=config.NARRATIVE_MATCH_RAW_FLOOR,
            margin_ratio=config.NARRATIVE_MATCH_MARGIN_RATIO,
        )
        # Which narrative BM25 actually wants. NOT `search_results[0]`: the
        # participant merge above appends entries with a synthetic 0.5
        # similarity and re-sorts on that, so position 0 can be a narrative
        # that never went through bm25 at all. The bypass rule has to compare
        # the KEYWORD winner against the anchor or it would compare noise.
        keyword_leader = max(
            search_results, key=lambda r: r.raw_score, default=None
        )
        bypass = evaluate_bypass(
            gate,
            top1_narrative_id=(
                keyword_leader.narrative_id
                if keyword_leader is not None and keyword_leader.raw_score > 0
                else None
            ),
            anchor_narrative_id=anchor_narrative_id,
            is_user_chat=is_user_chat,
            has_participant_narratives=bool(participant_narratives),
        )
        return ScoredPool(
            search_results=search_results,
            participant_narratives=participant_narratives,
            gate=gate,
            bypass=bypass,
            candidates=candidates,
            snapshots=snapshots,
            # "Pool read + ranking, and nothing else" — the documented meaning
            # of `keyword_ms`, now produced in one place so it cannot mean
            # something different on a shadow row.
            keyword_ms=_pool_ms + _rank_ms,
            best_score=(search_results[0].similarity_score
                        if search_results else None),
            all_scores={r.narrative_id: r.similarity_score
                        for r in search_results},
        )

    async def record_pool_only(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        *,
        top_k: int,
        anchor_narrative_id: Optional[str],
        is_user_chat: bool,
        audit: "RoutingAudit",
        snapshots: dict,
    ) -> None:
        """Score the pool for the RECORD, deciding nothing (slice 0).

        `NarrativeService.select` returns before the retrieval tier whenever
        continuity says yes, so those turns have never carried a pool. That is
        the single reason the zero-LLM shutter's releasable population is only
        bounded at 6%-39% of continuity turns: a 3x band that is
        reconstruction slack rather than signal
        (`reference/self_notebook/specs/2026-08-25-merged-routing-design.md`
        §2.2). This closes the gap by running the same scoring pass the real
        path runs, and handing the result to nobody.

        ALL-OR-NOTHING. Everything that can fail happens before the first line
        of the commit block below, so a failure leaves the audit row exactly as
        it was — no half-filled pool, and no orphan rows in
        `narrative_text_snapshots`. There is deliberately no "reset the fields
        the recorder wrote" list: such a list has to be updated by hand every
        time a column is added, and the one this replaces was already missing
        `gate_reason` on the day it was written.

        Cost is the same two reads the real path pays, PLUS what the recording
        itself newly triggers downstream: the audit write's snapshot dedup (one
        SELECT over the pool's hashes; steady state ~1 INSERT, a cold pool's
        first turn up to ~100) and a full-pool candidates_json on the row
        (~100 entries, 10KB-scale) — capacity, not latency, is the real line
        item. Everything is AWAITED, not fired and forgotten: a bare
        `create_task` here would swallow its own exceptions into a GC warning
        and race the audit write that is supposed to carry its output
        (incident lesson #2). The elapsed time lands in
        `audit.retrieve_ms`, which is empty on shadow rows today and whose
        meaning — how long the retrieval tier took — fits exactly, so the
        instrument carries its own L3 observability instead of a hand-measured
        number in a docstring.
        """
        _t_retrieve = _perf.monotonic()
        scored = await self._score_pool(
            query, user_id, agent_id,
            top_k=top_k,
            anchor_narrative_id=anchor_narrative_id,
            is_user_chat=is_user_chat,
        )
        elapsed_ms = int((_perf.monotonic() - _t_retrieve) * 1000)

        # ── commit block: pure assignment, cannot raise ──────────────────
        # gate_short_circuit=None on purpose — see below.
        commit_scored_pool(
            audit, snapshots, scored,
            retrieve_ms=elapsed_ms,
            is_shadow=True,
            gate_short_circuit=None,
        )
        # `gate_short_circuit` is NOT set. It means "the gate skipped the
        # judge", and here the gate decided nothing — filling it would redefine
        # the column for every existing reader (binding rule #6). Populations
        # are told apart by `pool_is_shadow` together with `is_user_chat` —
        # the instrument is scoped to user-chat turns, so a background
        # continuation row is an honest 0 here, never a poolless 1.

    async def load_pool(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[Tuple[str, str, bool]]:
        """The BM25 candidate pool: (narrative_id, scored_text, is_default).

        Split out of ``keyword_search`` so the routing audit can persist the
        WHOLE pool with the exact text that was scored. That completeness is
        load-bearing, not defensive: ``bm25_rank`` computes IDF and avgdl over
        the set it is handed, so a candidate's score depends on every other
        document present — including the eight default narratives, which are
        semantically irrelevant yet moved top-1 on 9.7% of 452 replayed local
        queries (2026-08-07). Re-deriving the pool later cannot work either:
        the scored text is rewritten wholesale by the async LLM updater with
        no history kept.
        """
        narratives = await self._crud.load_by_agent_user(
            agent_id, user_id, limit=config.NARRATIVE_POOL_LIMIT
        )
        keep_buckets = config.NARRATIVE_DEFAULT_BUCKETS_ENABLED
        return [
            (n.id, n.searchable_text(), n.is_special == "default")
            for n in narratives
            # C-1: a bucket's searchable_text is a frozen factory template, so
            # it can never legitimately WIN a query — but it still shifts every
            # other candidate's score through IDF/avgdl, and it can short-
            # circuit the gate on its own (measured: 2 turns in the replay).
            # Dropping it from the pool is what makes the rest of the batch
            # honest. Existing rows stay in the DB; only routing stops seeing
            # them.
            if keep_buckets or n.is_special != "default"
        ]

    @classmethod
    def rank_pool(
        cls,
        query: str,
        pool: List[Tuple[str, str, bool]],
        top_k: int,
    ) -> List[NarrativeSearchResult]:
        """Top-k slice of `rank_pool_full`, snippets included — the public
        shape `keyword_search` / `select_fast` and the replay tools consume."""
        return cls.rank_pool_full(query, pool, snippet_depth=top_k)[:top_k]

    @staticmethod
    def rank_pool_full(
        query: str,
        pool: List[Tuple[str, str, bool]],
        snippet_depth: int,
    ) -> List[NarrativeSearchResult]:
        """Rank an already-loaded pool — the FULL matched set, in one pass.

        Pure — no DB, so the audit replay and the live decision run
        byte-identical code.

        The whole ranking is kept (review round 3, I1): a truncated slice
        recorded rank-7+ candidates as raw_score 0.0, which made the two
        arms' `candidates_json` carry different precision — the exact
        cross-arm comparison this batch exists for — and could not tell "the
        anchor ranked below the cut" from "the anchor scored nothing" in the
        §3.2 instrument. Ranking 100 rows is cheap; what was NOT cheap was
        `bm25_snippet` per row (a full text.lower() copy of searchable_text,
        p99 in the KB range), so snippets — evidence for the PROMPT, nothing
        else — are computed only for the first `snippet_depth` rows. A menu
        row can still be drawn from beyond this window (`pick_menu` excludes
        the anchor and scoring participants AFTER this ordering), so
        `build_menu_candidates` backfills such a row's snippet lazily — every
        row a model sees carries its evidence either way (round 6, I2).

        Ranks via ``bm25_explain`` rather than ``bm25_rank``: same arithmetic
        and the same score to the last bit, but it also hands back WHICH query
        terms earned that score. That evidence travels to the LLM judge
        (`_llm_unified_match`), and this is the only moment it is free — the
        scored text is in hand here, and reconstructing it later is impossible
        because the async updater rewrites it wholesale with no history.

        The routing prefix is stripped HERE, not at the call sites, so every
        BM25 consumer inherits it — `retrieve_top_k`, `keyword_search`, and
        `select_fast` through it — and so a replay that goes through this
        method still reproduces the live decision byte for byte. The audit row
        keeps the ORIGINAL query text: what the user said is the record, and
        what BM25 scored is derivable from it by this same function.
        """
        from narranexus.platform.memory.bm25 import (
            bm25_explain,
            bm25_snippet,
        )

        # "[From <sender>] " is routing metadata, not topic evidence: it is
        # present on 96% of prod queries and carries 100% of the score on the
        # worst of them (audit 768, `[From Liam] 👊` -> 5.66 from `from`+`liam`
        # alone, the emoji tokenising to nothing). The judge and the continuity
        # tier still read the untouched text — they can tell a name from a
        # topic, BM25 cannot. See utils.text.strip_routing_prefix.
        explained = bm25_explain(
            strip_routing_prefix(query), [(nid, text) for nid, text, _ in pool]
        )
        texts = {nid: text for nid, text, _ in pool}
        ranked = sorted(
            explained.items(), key=lambda kv: kv[1][0], reverse=True
        )
        results = []
        for i, (nid, (score, contributions)) in enumerate(ranked):
            terms = [term for term, _ in contributions]
            results.append(NarrativeSearchResult(
                narrative_id=nid,
                similarity_score=score / (score + 1.0),
                rank=i + 1,
                raw_score=score,
                matched_terms=terms[:MAX_MATCHED_TERMS],
                matched_snippet=(
                    bm25_snippet(texts[nid], terms) if i < snippet_depth else ""
                ),
            ))
        return results

    async def keyword_search(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        top_k: int,
    ) -> List[NarrativeSearchResult]:
        """BM25 keyword retrieval over the agent's narratives — the non-vector
        BM25 keyword search over the agent's narratives.

        Ranks each narrative by query overlap on `Narrative.searchable_text()`
        (the ONE definition of the retrieval surface — restating the field list
        here is how it drifted last time), using the same BM25 the MemoryEngine
        uses.
        Scores are normalized monotonically into (0,1) so the existing two-tier
        threshold still applies: weak matches fall through to the LLM tier;
        strong keyword matches may direct-return.

        Public seam: ``NarrativeService.select_fast`` (F28) depends on this
        signature. ``retrieve_top_k`` uses ``load_pool`` + ``rank_pool_full``
        directly so it can keep the pool for the audit.
        """
        return self.rank_pool(query, await self.load_pool(agent_id, user_id), top_k)

    async def _llm_unified_match(
        self,
        query: str,
        search_results: List[NarrativeSearchResult],
        agent_id: str,
        user_id: str,
        top_k: int,
        narrative_type: NarrativeType,
        best_score: Optional[float],
        participant_narratives: Optional[List[Narrative]] = None,  # P0-4: PARTICIPANT Narratives
        retrieval_method: str = "",  # Retrieval method identifier
        audit: Optional["RoutingAudit"] = None,  # E1: filled in place with the judge verdict
    ) -> NarrativeSelectionResult:
        """
        LLM unified judgment: Considers search results, default Narratives, and PARTICIPANT Narratives

        Uses NarrativeRepository.get_default_narratives() method to get default Narratives,
        avoiding direct SQL in business logic.

        Workflow:
        1. Load searched Narratives and default Narratives
        2. (P0-4) Load PARTICIPANT Narratives (topics where user is a PARTICIPANT)
        3. Call LLM to determine which one the user query matches
        4. Based on match result:
           a) Match PARTICIPANT -> Return with priority (PARTICIPANT task priority)
           b) Match default type -> Return 1 default Narrative
           c) Match search result -> Return Top-K list
           d) No match -> Create new Narrative

        Args:
            query: User query
            search_results: BM25 keyword search results
            agent_id: Agent ID
            user_id: User ID
            top_k: Number of results to return
            narrative_type: Narrative type
            best_score: Best match score
            participant_narratives: P0-4 - Narratives where user is a PARTICIPANT

        Returns:
            NarrativeSelectionResult
        """
        # 1. Prepare search result candidates (narrative metadata only)
        all_scores = {r.narrative_id: r.similarity_score for r in search_results}
        search_candidates = []

        for result in search_results:
            narrative = await self._crud.load_by_id(result.narrative_id)
            if narrative:
                candidate_name, candidate_desc = candidate_labels(narrative)

                # The BM25 evidence, carried through from rank_pool. Without it
                # the judge sees only `Similarity score: 0.91` — a number that
                # can be 100% request-frame characters, on the very turns the
                # gate handed over BECAUSE the candidates were crowded.
                #
                # `raw_score` rides along un-rendered, as the marker for "this
                # candidate came from BM25 and therefore OWES evidence": step
                # 1.5 merges participant narratives into `search_results` at a
                # synthetic 0.5 similarity, so this list is not purely
                # BM25-sourced and the missing-evidence alarm downstream would
                # otherwise cry wolf on every participant turn.
                search_candidates.append({
                    "id": narrative.id,
                    "type": "search",
                    "name": candidate_name,
                    "description": candidate_desc,
                    "score": result.similarity_score,
                    "raw_score": result.raw_score,
                    "matched_terms": result.matched_terms,
                    "matched_content": result.matched_snippet,
                })

        logger.debug(f"[NarrativeSelect] Prepared {len(search_candidates)} search candidates for LLM judge")

        # 2. Use Repository to get default Narrative candidates (lazy import to avoid circular dependency)
        from narranexus.platform.repository import NarrativeRepository
        db_client = await get_db_client()
        repo = NarrativeRepository(db_client)
        # C-1: with buckets governed, the judge gets real threads only. The
        # eight category names move into the instructions as vocabulary (see
        # prompts.NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS) — a menu of eight fixed
        # entries WITH worked examples against at most three dynamic ones was a
        # menu that answered itself (measured: 60% of judge verdicts picked a
        # bucket, and 63 of those 93 had a real candidate available).
        default_narratives = (
            await repo.get_default_narratives(agent_id, user_id)
            if config.NARRATIVE_DEFAULT_BUCKETS_ENABLED
            else []
        )

        default_candidates = []
        for narrative in default_narratives:
            # Get examples from configuration
            config_item = next(
                (c for c in DEFAULT_NARRATIVES_CONFIG if c["name"] == narrative.narrative_info.name),
                None
            )

            default_candidates.append({
                "id": narrative.id,
                "type": "default",
                "name": narrative.narrative_info.name,
                "description": narrative.narrative_info.description,
                "examples": config_item["examples"] if config_item else []
            })

        # 2.5 (P0-4): Prepare PARTICIPANT Narrative candidates
        participant_candidates = []
        if participant_narratives:
            for narrative in participant_narratives:
                # Same labeller as the search branch above — no matched_terms /
                # matched_content: these never went through BM25 (they enter at
                # a synthetic neutral score), and inventing evidence for them
                # would be worse than showing none.
                candidate_name, candidate_desc = candidate_labels(narrative)
                participant_candidates.append({
                    "id": narrative.id,
                    "type": "participant",  # P0-4: Changed to "participant"
                    "name": candidate_name,
                    "description": candidate_desc,
                })
            logger.info(f"P0-4: Added {len(participant_candidates)} PARTICIPANT candidates to LLM judgment")

        # 3. Call LLM for unified judgment
        llm_result = await self._llm_judge_unified(
            query=query,
            search_candidates=search_candidates,
            default_candidates=default_candidates,
            participant_candidates=participant_candidates  # P0-4: Pass PARTICIPANT candidates
        )

        # E1: the judge's verdict AND its reasoning. This is the only semantic
        # check in the pipeline, and its reasoning previously survived only
        # inside an f-string that went to loguru.
        if audit is not None:
            audit.judge_ran = True
            audit.judge_category = llm_result.get("matched_type") or "none"
            audit.judge_matched_id = llm_result.get("matched_id")
            audit.judge_reason = llm_result.get("reason") or ""

        # 4. Return based on LLM judgment result
        if llm_result["matched_id"]:
            matched_type = llm_result["matched_type"]
            matched_id = llm_result["matched_id"]
            reason = llm_result["reason"]

            if matched_type == "default":
                # Matched a default Narrative, return only this 1
                logger.info(f"LLM matched default Narrative: {matched_id}")
                matched_narrative = await self._crud.load_by_id(matched_id)

                return NarrativeSelectionResult(
                    narratives=[matched_narrative] if matched_narrative else [],
                    selection_reason=f"LLM matched default Narrative: {reason}",
                    selection_method="default_narrative_matched",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    # evermemos_memories removed — EverMemOS decoupled from narrative selection
                )

            elif matched_type == "participant":
                # P0-4: Matched a PARTICIPANT Narrative (task priority)
                logger.info(f"LLM matched PARTICIPANT Narrative: {matched_id}")
                participant_landing = await load_participant_landing(self._crud, matched_id)

                return NarrativeSelectionResult(
                    narratives=participant_landing,
                    selection_reason=f"LLM matched PARTICIPANT Narrative: {reason}",
                    selection_method="participant_narrative_matched",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    # evermemos_memories removed — EverMemOS decoupled from narrative selection
                )

            elif matched_type == "search":
                # Matched a search result, return Top-K list. The assembly moved
                # into `assemble_match_landing` so the merged router lands its
                # own `match` verdict through this exact executor.
                logger.info(f"LLM matched search result: {matched_id}")
                narratives = await assemble_match_landing(
                    self._crud,
                    matched_id, search_results, top_k
                )

                return NarrativeSelectionResult(
                    narratives=narratives,
                    selection_reason=f"LLM matched search result: {reason}",
                    selection_method="llm_confirmed",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    # evermemos_memories removed — EverMemOS decoupled from narrative selection
                )

        # 4.5 (C-1) "No durable topic" — a verdict about the TURN, not a
        # destination. The retrieval tier deliberately stops here with an empty
        # list instead of creating: where such a turn lands depends on the
        # session anchor and on whether the surface persists history, and
        # neither is knowable from inside retrieval. NarrativeService.select
        # owns that decision (anchor-first). Creating here is exactly the
        # fragmentation this batch exists to avoid — a "你好" must not open a
        # thread while the user's real work thread is one lookup away.
        if llm_result.get("matched_type") == "no_topic":
            logger.info("LLM: no durable topic this turn — deferring the landing")
            return NarrativeSelectionResult(
                narratives=[],
                selection_reason=f"No durable topic: {llm_result.get('reason', '')}",
                selection_method="no_topic",
                is_new=False,
                no_durable_topic=True,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
            )

        # 5. No match, create new Narrative
        logger.info("LLM determined no match with any Narrative, creating new topic")
        new_narrative = await self.create_from_query(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            narrative_type=narrative_type
        )

        return NarrativeSelectionResult(
            narratives=[new_narrative],
            selection_reason=f"LLM determined new topic: {llm_result.get('reason', 'No match')}",
            selection_method="new_created",
            is_new=True,
            best_score=best_score,
            scores=all_scores,
            retrieval_method=retrieval_method,
        )

    async def _llm_judge_unified(
        self,
        query: str,
        search_candidates: List[dict],
        default_candidates: List[dict],
        participant_candidates: Optional[List[dict]] = None,
    ) -> dict:
        """LLM unified judgment — delegates to _retrieval_llm module"""
        return await llm_judge_unified(
            query=query,
            search_candidates=search_candidates,
            default_candidates=default_candidates,
            participant_candidates=participant_candidates,
        )

    async def _get_participant_narratives(
        self,
        user_id: str,
        agent_id: str
    ) -> List[Narrative]:
        """
        Query Narratives where the user is a PARTICIPANT (2026-01-21 P0-4)

        Core logic:
        - Directly query Narratives whose actors contain {id: user_id, type: PARTICIPANT}
        - More direct and efficient than the previous Entity -> Job -> Narrative path

        Use cases:
        - Any scenario where non-Creator users need access to specific Narratives
        - Specific meaning (e.g., sales target, collaborator) is defined by the Agent's Awareness

        Args:
            user_id: User ID
            agent_id: Agent ID

        Returns:
            List of Narratives (all Narratives where the user is a PARTICIPANT)
        """
        import asyncio

        try:
            from narranexus.platform.repository import NarrativeRepository

            db_client = await get_db_client()
            repo = NarrativeRepository(db_client)

            # Use Repository to query Narratives where user is a PARTICIPANT
            narratives = await repo.get_narratives_by_participant(
                user_id=user_id,
                agent_id=agent_id
            )

            if narratives:
                logger.info(f"PARTICIPANT Narratives: User {user_id} is a PARTICIPANT in {len(narratives)} Narratives")
            else:
                logger.debug(f"PARTICIPANT Narratives: User {user_id} has no PARTICIPANT Narratives")

            return narratives

        except Exception as e:
            logger.exception(f"PARTICIPANT Narratives: Query failed: {e}")
            return []

    async def create_from_query(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        narrative_type: NarrativeType
    ) -> Narrative:
        """Create a new Narrative from the query (BM25 routing surface only).

        NAME and DESCRIPTION are built from the query with the channel routing
        prefix removed. Naming a thread "[From <sender>] ..." turns it into a
        magnet for every later message from that channel: the sender tokens
        then sit in the thread's OWN retrieval surface at a low in-pool df, so
        the next message matches its own line and skips the judge (prod audit
        1492 reached margin 357.79 exactly this way; four such lines exist in
        prod today). `topic_keywords` is deliberately left on the raw query —
        that field has an undecided two-writer design (A-kw) and is read-only
        for this change.
        """
        # Extract keywords (the BM25 routing surface)
        topic_keywords = extract_keywords(query)

        # The naming surface, without the channel label. Falls back to the raw
        # query when a message is nothing BUT a prefix — an unnamed thread is
        # worse than a slightly noisy name.
        naming_text = strip_routing_prefix(query).strip() or query

        # Generate topic hint
        topic_hint = truncate_text(naming_text, config.SUMMARY_MAX_LENGTH)

        # Generate title
        title = truncate_text(naming_text, 30)

        # And the description. Its two siblings above were truncated and it was
        # not, which is how prod ended up with a 198,398-character description
        # welded into a BM25 index that the updater never rewrites. Bounded here
        # AND clamped again in `_crud.create` — the call site states the intent,
        # the funnel covers the other two writers (the LLM's create_narrative
        # signal and the HTTP route).
        description = truncate_text(
            f"Created based on query: {naming_text}", config.DESCRIPTION_MAX_LENGTH
        )

        # Create Narrative
        narrative = await self._crud.create(
            agent_id=agent_id,
            user_id=user_id,
            narrative_type=narrative_type,
            title=title,
            description=description
        )

        # BM25 routing surface. `searchable_text()` is the one definition:
        # name + current_summary + (description, only while unsummarised) +
        # topic_keywords. The old comment here listed three fields and omitted
        # description, which is how it stayed a tombstone in the index for two
        # months without anyone noticing. Embedding
        # fields (routing_embedding / embedding_updated_at / VectorStore /
        # embeddings_store) are retired — narrative routing is vector-free.
        narrative.topic_keywords = topic_keywords
        narrative.topic_hint = topic_hint

        await self._crud.save(narrative)

        logger.info(f"Created new Narrative: {narrative.id}")
        return narrative
