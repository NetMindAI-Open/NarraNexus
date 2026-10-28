"""
@file_name: landings.py
@author: NetMind.AI
@date: 2026-08-27
@description: The executors every routing decider shares — candidate
              labelling for prompts, and the landings for match / participant
              verdicts — plus the Landing value object.

Extracted from retrieval.py on review (2026-08-27 round 3, I3): the four
methods were a cohesive unit ("change the decider, keep the executors") lost
inside a 1,300-line module, and `Landing` lived in merged_select — which
forced the flag-off path to import the whole merged module (and its
helper-SDK chain) for a six-field dataclass (M4).

`candidate_labels` moved WITH its four consumers — it is the ONE definition
of what a candidate shows a model, and it must not fork (the judge's two
branches were two copies of that decision once, and only one was fixed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, TYPE_CHECKING

from ..models import Narrative, NarrativeSearchResult, NarrativeType

if TYPE_CHECKING:
    from ..models import ConversationSession
    from .crud import NarrativeCRUD
    from .retrieval import NarrativeRetrieval


CANDIDATE_DESC_MAX_CHARS = 300  # Summary excerpt shown per candidate


@dataclass(frozen=True)
class Landing:
    """Where one turn landed — every field a verdict must answer, at once.

    Constructed whole by every verdict branch (merged path) and by
    `_land_no_topic_turn` (both paths): the alternative — six loose locals
    assigned per-branch — is how a new result field gets set on four verdicts
    and silently defaulted on the fifth.
    """

    narratives: List[Narrative]
    method: str
    reason: str
    retrieval_method: str
    is_new: bool = False
    no_durable_topic: bool = False


def candidate_labels(narrative: Narrative) -> Tuple[str, str]:
    """The (name, description) a narrative shows the LLM judge — ONE definition.

    Every branch that assembles a judge candidate goes through here. That is
    the actual fix, not an aesthetic one: the search branch and the PARTICIPANT
    branch of `_llm_unified_match` were two implementations of this same
    decision, 50 lines apart, and on 2026-04-15 only the search branch was
    moved onto the live `narrative_info` fields. The PARTICIPANT branch kept
    reading `topic_hint`, which the 2026-06-09 unified-memory refactor then
    froze into a write-once-at-creation tombstone — 84% empty on the local dev
    DB, and stale wherever it is not. Measured worst cases: a 72-event
    narrative described to the judge by its first sentence from three months
    earlier, and one whose label was a `[:50]` cut through the middle of an
    open_id. That branch FORCES the judge to run (a task someone invited the
    user into must not lose to a keyword hit on the user's own narrative), so
    a blind label there decides the turn.

    "Untitled" with an empty description is the honest answer for a narrative
    whose metadata the async updater has not written yet; a frozen creation-time
    hint is not, because it reads to the LLM as current fact.
    """
    info = narrative.narrative_info
    name = (info.name if info and info.name else "") or "Untitled"
    summary = (info.current_summary if info and info.current_summary else "")
    return name, summary[:CANDIDATE_DESC_MAX_CHARS]


async def build_menu_candidates(
    crud: "NarrativeCRUD", results: Sequence[NarrativeSearchResult]
) -> List[dict]:
    """Load and label the menu rows a routing prompt will show.

    Goes through `candidate_labels` — the ONE definition of what a
    candidate shows a model. The judge's two branches were two copies of
    that decision once, and only one of them was ever fixed; a third copy
    here is how that repeats.
    """
    from narranexus.platform.memory.bm25 import bm25_snippet

    candidates: List[dict] = []
    for result in results:
        narrative = await crud.load_by_id(result.narrative_id)
        if narrative is None:
            continue
        name, description = candidate_labels(narrative)
        matched_content = result.matched_snippet
        if not matched_content and result.raw_score > 0 and result.matched_terms:
            # A menu row from beyond the snippet head (review round 6, I2):
            # `rank_pool_full` computes snippets only for the top rows of the
            # BM25 ordering, but `pick_menu` selects AFTER excluding the
            # anchor and scoring participants — exclude 4+ of the top 6 and a
            # menu row arrives snippet-less, showing terms without the
            # load-bearing half of the evidence and tripping the wiring-broken
            # alarm downstream. Computing it here is exact and costs at most
            # menu_size snippet calls, only on merged turns that hit the case.
            matched_content = bm25_snippet(
                narrative.searchable_text(), list(result.matched_terms)
            )
        candidates.append({
            "id": narrative.id,
            "type": "search",
            "name": name,
            "description": description,
            "score": result.similarity_score,
            "raw_score": result.raw_score,
            "matched_terms": result.matched_terms,
            "matched_content": matched_content,
        })
    return candidates


def build_participant_candidates(
    narratives: Sequence[Narrative],
) -> List[dict]:
    """Label PARTICIPANT threads for a prompt. Same labeller, and
    deliberately no evidence fields: these never went through BM25 (they
    enter at a synthetic neutral score), and inventing evidence for them
    would be worse than showing none."""
    candidates: List[dict] = []
    for narrative in narratives:
        name, description = candidate_labels(narrative)
        candidates.append({
            "id": narrative.id,
            "type": "participant",
            "name": name,
            "description": description,
        })
    return candidates


async def assemble_match_landing(
    crud: "NarrativeCRUD",
    matched_id: str,
    search_results: Sequence[NarrativeSearchResult],
    top_k: int,
) -> List[Narrative]:
    """The chosen thread first, then the rest of the ranked set.

    Extracted from `_llm_unified_match`'s search branch so the merged router
    lands a `match` verdict through the SAME executor. The whole shape of
    this batch is "change the decider, keep the executors" — a second copy
    of this loop would be the first crack in that.
    """
    narratives: List[Narrative] = []
    matched = await crud.load_by_id(matched_id)
    if matched:
        narratives.append(matched)
    for result in search_results[:top_k]:
        if result.narrative_id == matched_id:
            continue
        narrative = await crud.load_by_id(result.narrative_id)
        if narrative and len(narratives) < top_k:
            narratives.append(narrative)
    return narratives


async def load_participant_landing(
    crud: "NarrativeCRUD", matched_id: Optional[str]
) -> List[Narrative]:
    """The participant verdict's landing — one loader, both deciders.

    Same reasoning as `assemble_match_landing`: the judge and the merged
    router must land a participant verdict through the SAME executor, or
    the first added line (trailing context, surface guard) forks them.
    """
    matched = await crud.load_by_id(matched_id) if matched_id else None
    return [matched] if matched else []


async def land_no_topic(
    crud: "NarrativeCRUD",
    retrieval: "NarrativeRetrieval",
    *,
    agent_id: str,
    user_id: str,
    query_text: str,
    session: Optional["ConversationSession"],
    reason: str,
    anchor: Optional[Narrative] = None,
) -> Landing:
    """Place a turn the decider called "no durable topic" (C-1, plan 4-A').

    The decider answered a question about the TURN ("is there anything here
    worth remembering as its own thread?"); it did not name a destination.
    This is where the destination is decided, and the rule is anchor-first —
    deliberately the same shape ``step_1_fast_select`` settled on for the
    fast path, so the paths cannot drift into disagreeing about where a
    contentless turn belongs:

    1. **A live anchor on a real thread → reuse it.** A "你好" in the middle
       of a task belongs to the task (annotation protocol R1), and the user
       expects the agent to still know what they were doing. The thread's
       retrieval surface is NOT touched — see ``no_durable_topic`` on
       NarrativeSelectionResult for why a greeting must never get to rename
       the work it interrupted.
    2. **No anchor → create.** Chat history endpoints are narrative-scoped
       and the ChatModule instance hangs off the narrative, so running bare
       would make a first-contact turn vanish from the user's own history.
       The created thread is not junk: it becomes the anchor, and the updater
       renames it as the real subject emerges.

    ``anchor`` is an optimisation only (round 5, M2): the merged path already
    holds the loaded object, the flag-off path passes None and it is read
    here. Reusability is judged HERE either way — trusting a caller's
    "continuable" flag would fork the bucket rule between the two paths.

    (An `ephemeral → run bare` third branch was removed on review 2026-08-21,
    Important #2: unreachable in production — every ephemeral TurnProfile
    takes the fast path. Re-add deliberately from TurnProfile if that changes.)

    Moved from NarrativeService on review (round 5, I3): it is a landing
    executor, and its four siblings already live in this module.
    """
    from loguru import logger

    from .anchor_rules import is_reusable_anchor

    anchor_id = getattr(session, "current_narrative_id", None) if session else None
    if anchor_id:
        anchored = anchor if (anchor is not None and anchor.id == anchor_id) \
            else await crud.load_by_id(anchor_id)
        # A bucket anchor is not a thread to reuse (slice 5 already refused
        # to continue one); fall through and let the turn land properly.
        if is_reusable_anchor(anchored):
            logger.info(
                f"[NarrativeSelect] no durable topic — reusing anchor "
                f"{anchored.id} without touching its surface"
            )
            return Landing(
                narratives=[anchored],
                method="no_topic_anchored",
                reason=f"No durable topic; kept on the active thread: {reason}",
                retrieval_method="keyword",
                is_new=False,
                no_durable_topic=True,
            )

    created = await retrieval.create_from_query(
        query=query_text,
        user_id=user_id,
        agent_id=agent_id,
        narrative_type=NarrativeType.CHAT,
    )
    logger.info(
        f"[NarrativeSelect] no durable topic and no anchor — created "
        f"{created.id} so the turn stays in history"
    )
    return Landing(
        narratives=[created],
        method="new_created",
        reason=f"No durable topic, no thread to keep it on: {reason}",
        retrieval_method="keyword",
        is_new=True,
        no_durable_topic=True,
    )
