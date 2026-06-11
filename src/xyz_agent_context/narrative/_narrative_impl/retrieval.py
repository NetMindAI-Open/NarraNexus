"""
Narrative retrieval implementation

@file_name: retrieval.py
@author: NetMind.AI
@date: 2025-12-22
@description: BM25 keyword retrieval + LLM unified match for narrative routing.
"""

from __future__ import annotations

from typing import List, Optional, Tuple, TYPE_CHECKING

from loguru import logger

from ..config import config
from ..models import (
    Narrative,
    NarrativeSearchResult,
    NarrativeSelectionResult,
    NarrativeType,
)
from .crud import NarrativeCRUD
from .default_narratives import (
    DEFAULT_NARRATIVES_CONFIG,
    ensure_default_narratives,
    build_default_narrative_id_pattern,
)
from xyz_agent_context.utils.logging import timed

# Use common utilities from utils
from xyz_agent_context.utils.text import extract_keywords, truncate_text
from xyz_agent_context.utils.db_factory import get_db_client
from ._retrieval_llm import (
    RelationType,
    NarrativeMatchOutput,
    UnifiedMatchOutput,
    llm_confirm,
    llm_judge_unified,
)

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient
    from xyz_agent_context.repository import NarrativeRepository


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
        narrative_type: NarrativeType = NarrativeType.CHAT
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

        # Step 0: Ensure default Narratives exist
        with timed("narrative.retrieve.ensure_defaults"):
            await self._ensure_default_narratives(agent_id, user_id)

        # Step 0.5 (P0-4): Query Narratives where user is a PARTICIPANT
        # Replaces the previous _get_narratives_by_entity_jobs(), queries directly via actors
        with timed("narrative.retrieve.participant_query"):
            participant_narratives = await self._get_participant_narratives(
                user_id=user_id,
                agent_id=agent_id
            )
        has_participant_narratives = len(participant_narratives) > 0
        if has_participant_narratives:
            logger.info(f"P0-4: User is a PARTICIPANT in {len(participant_narratives)} Narratives")

        # Step 1: Search for candidate Narratives by KEYWORD (BM25 over each
        # narrative's name + summary + topic keywords). BM25 casts the net
        # over the agent's real narratives — including non-default ones — then
        # the LLM unified-match tier below arbitrates. Reuses the same BM25 the
        # MemoryEngine uses, so narrative routing and memory recall share one
        # ranking implementation. Zero vectors.
        with timed("narrative.retrieve.keyword_search"):
            search_results = await self._keyword_search(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                top_k=max(top_k * 2, config.NARRATIVE_SEARCH_TOP_K),
            )
        retrieval_method = "keyword"
        logger.info(f"[NarrativeSelect] Keyword(BM25) search returned {len(search_results)} candidates")

        # Step 1.5 (P0-4): Add PARTICIPANT Narratives to candidate list (if not already in search results)
        # This is key: participant_narratives come from Narratives created by other users; keyword search won't return them
        existing_narrative_ids = {r.narrative_id for r in search_results}
        for narrative in participant_narratives:
            if narrative.id not in existing_narrative_ids:
                # Embeddings retired: participant narratives enter the candidate
                # pool with a neutral score; the LLM unified-match tier below
                # arbitrates their relevance. (No cosine scoring.)
                search_results.append(NarrativeSearchResult(
                    narrative_id=narrative.id,
                    similarity_score=0.5,
                    rank=999
                ))
                logger.info(f"  Added PARTICIPANT Narrative: {narrative.id} (neutral score 0.5)")

        # Re-sort (by similarity descending) and update rank
        search_results.sort(key=lambda x: x.similarity_score, reverse=True)
        for i, result in enumerate(search_results):
            result.rank = i + 1

        # Step 2: Two-tier threshold judgment
        best_score = search_results[0].similarity_score if search_results else None
        all_scores = {r.narrative_id: r.similarity_score for r in search_results}

        # First tier: High confidence - Return Top-K directly
        # P0-4 improvement: If user has PARTICIPANT Narratives, still go through LLM judgment even with high confidence
        # Reason: High confidence may match user's own Narrative, but should actually match the PARTICIPANT-associated task
        if best_score and best_score >= config.NARRATIVE_MATCH_HIGH_THRESHOLD and not has_participant_narratives:
            logger.info(f"High confidence match (score={best_score:.2f}), returning Top-{top_k} directly")
            narratives = []
            for result in search_results[:top_k]:
                narrative = await self._crud.load_by_id(result.narrative_id)
                if narrative:
                    narratives.append(narrative)

            return NarrativeSelectionResult(
                narratives=narratives,
                selection_reason=f"High confidence match, BM25 score {best_score:.2f} >= {config.NARRATIVE_MATCH_HIGH_THRESHOLD}",
                selection_method="high_confidence",
                is_new=False,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
                # evermemos_memories removed — EverMemOS decoupled from narrative selection
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
                    retrieval_method=retrieval_method  # Pass retrieval method
                )
                # Tag with the model + structured-output mode the SDK
                # ended up using inside _llm_unified_match → llm_judge_unified
                # → sdk.llm_function. See openai_agents_sdk.get_last_llm_call_info.
                from xyz_agent_context.agent_framework.openai_agents_sdk import (
                    get_last_llm_call_info,
                )
                info = get_last_llm_call_info()
                if info:
                    t.tag(**info)
                return result

        # LLM not enabled - Create new Narrative directly
        else:
            logger.info("LLM not enabled, creating new topic directly")
            new_narrative = await self._create_narrative(
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
        # Use Repository to check if default Narratives already exist (lazy import to avoid circular dependency)
        from xyz_agent_context.repository import NarrativeRepository
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

    async def _keyword_search(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        top_k: int,
    ) -> List[NarrativeSearchResult]:
        """BM25 keyword retrieval over the agent's narratives — the non-vector
        BM25 keyword search over the agent's narratives.

        Ranks each narrative by query overlap on its name + current_summary +
        description + topic_keywords, using the same BM25 the MemoryEngine uses.
        Scores are normalized monotonically into (0,1) so the existing two-tier
        threshold still applies: weak matches fall through to the LLM tier;
        strong keyword matches may direct-return.
        """
        from xyz_agent_context.memory._memory_impl.retrieval import bm25_rank

        narratives = await self._crud.load_by_agent_user(agent_id, user_id, limit=100)
        items: List[tuple] = []
        for n in narratives:
            info = n.narrative_info
            text = " ".join(
                p for p in (
                    getattr(info, "name", ""),
                    getattr(info, "current_summary", ""),
                    getattr(info, "description", ""),
                    " ".join(n.topic_keywords or []),
                ) if p
            )
            items.append((n.id, text))

        scores = bm25_rank(query, items)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [
            NarrativeSearchResult(narrative_id=nid, similarity_score=s / (s + 1.0), rank=i + 1)
            for i, (nid, s) in enumerate(ranked)
        ]

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
        retrieval_method: str = ""  # Retrieval method identifier
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
                # Use narrative_info for candidate info (no episode_summaries after decoupling)
                candidate_name = (
                    narrative.narrative_info.name
                    if narrative.narrative_info and narrative.narrative_info.name
                    else (narrative.topic_hint[:50] if narrative.topic_hint else "Untitled")
                )
                candidate_desc = (
                    narrative.narrative_info.current_summary[:300]
                    if narrative.narrative_info and narrative.narrative_info.current_summary
                    else (narrative.topic_hint[:100] if narrative.topic_hint else "")
                )

                search_candidates.append({
                    "id": narrative.id,
                    "type": "search",
                    "name": candidate_name,
                    "description": candidate_desc,
                    "score": result.similarity_score,
                })

        logger.debug(f"[NarrativeSelect] Prepared {len(search_candidates)} search candidates for LLM judge")

        # 2. Use Repository to get default Narrative candidates (lazy import to avoid circular dependency)
        from xyz_agent_context.repository import NarrativeRepository
        db_client = await get_db_client()
        repo = NarrativeRepository(db_client)
        default_narratives = await repo.get_default_narratives(agent_id, user_id)

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
                participant_candidates.append({
                    "id": narrative.id,
                    "type": "participant",  # P0-4: Changed to "participant"
                    "name": narrative.topic_hint[:50] if narrative.topic_hint else "Untitled",
                    "description": narrative.topic_hint[:100] if narrative.topic_hint else "",
                })
            logger.info(f"P0-4: Added {len(participant_candidates)} PARTICIPANT candidates to LLM judgment")

        # 3. Call LLM for unified judgment
        llm_result = await self._llm_judge_unified(
            query=query,
            search_candidates=search_candidates,
            default_candidates=default_candidates,
            participant_candidates=participant_candidates  # P0-4: Pass PARTICIPANT candidates
        )

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
                matched_narrative = await self._crud.load_by_id(matched_id)

                return NarrativeSelectionResult(
                    narratives=[matched_narrative] if matched_narrative else [],
                    selection_reason=f"LLM matched PARTICIPANT Narrative: {reason}",
                    selection_method="participant_narrative_matched",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    # evermemos_memories removed — EverMemOS decoupled from narrative selection
                )

            elif matched_type == "search":
                # Matched a search result, return Top-K list
                logger.info(f"LLM matched search result: {matched_id}")
                narratives = []
                matched_narrative = await self._crud.load_by_id(matched_id)
                if matched_narrative:
                    narratives.append(matched_narrative)

                # Add other candidates (excluding already matched)
                for result in search_results[:top_k]:
                    if result.narrative_id != matched_id:
                        narrative = await self._crud.load_by_id(result.narrative_id)
                        if narrative and len(narratives) < top_k:
                            narratives.append(narrative)

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

        # 5. No match, create new Narrative
        logger.info("LLM determined no match with any Narrative, creating new topic")
        new_narrative = await self._create_narrative(
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

    async def _prepare_candidates(
        self,
        search_results: List[NarrativeSearchResult]
    ) -> List[dict]:
        """Prepare candidate list for LLM confirmation"""
        candidates = []
        for result in search_results:
            narrative = await self._crud.load_by_id(result.narrative_id)
            if narrative:
                candidates.append({
                    "id": narrative.id,
                    "name": narrative.topic_hint[:30] if narrative.topic_hint else "Untitled",
                    "query": narrative.topic_hint[:50] if narrative.topic_hint else "",
                })
        return candidates

    async def _llm_confirm(self, query: str, candidates: List[dict]) -> dict:
        """LLM match confirmation — delegates to _retrieval_llm module"""
        return await llm_confirm(query, candidates)

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
            from xyz_agent_context.repository import NarrativeRepository

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

    async def _create_narrative(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        narrative_type: NarrativeType
    ) -> Narrative:
        """Create a new Narrative from the query (BM25 routing surface only)."""
        # Extract keywords (the BM25 routing surface)
        topic_keywords = extract_keywords(query)

        # Generate topic hint
        topic_hint = truncate_text(query, config.SUMMARY_MAX_LENGTH)

        # Generate title
        title = truncate_text(query, 30)

        # Create Narrative
        narrative = await self._crud.create(
            agent_id=agent_id,
            user_id=user_id,
            narrative_type=narrative_type,
            title=title,
            description=f"Created based on query: {query}"
        )

        # BM25 routing surface (name + summary + topic_keywords). Embedding
        # fields (routing_embedding / embedding_updated_at / VectorStore /
        # embeddings_store) are retired — narrative routing is vector-free.
        narrative.topic_keywords = topic_keywords
        narrative.topic_hint = topic_hint

        await self._crud.save(narrative)

        logger.info(f"Created new Narrative: {narrative.id}")
        return narrative
