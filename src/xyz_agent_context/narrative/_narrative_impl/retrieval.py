"""
Narrative retrieval implementation

@file_name: retrieval.py
@author: NetMind.AI
@date: 2025-12-22
@description: Vector retrieval, LLM confirmation, score enhancement
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
from .vector_store import VectorStore
from .crud import NarrativeCRUD
from .default_narratives import (
    DEFAULT_NARRATIVES_CONFIG,
    ensure_default_narratives,
    build_default_narrative_id_pattern,
)
from xyz_agent_context.utils.evermemos import get_evermemos_client

# Use common utilities from utils
from xyz_agent_context.agent_framework.llm_api.embedding import (
    get_embedding,
    cosine_similarity,
    compute_average_embedding,
)
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
    - Vector similarity search
    - LLM match confirmation
    - Recent Event score enhancement
    - Retrieve or create Narrative
    """

    def __init__(self, agent_id: str):
        """
        Initialize retrieval engine

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._crud = NarrativeCRUD(agent_id)
        self._vector_store = VectorStore()
        self._event_service = None  # Dependency injection

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._crud.set_database_client(db_client)

    def set_event_service(self, event_service):
        """Inject EventService"""
        self._event_service = event_service

    @property
    def vector_store(self) -> VectorStore:
        """Get the vector store"""
        return self._vector_store

    async def retrieve_or_create(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        narrative_type: NarrativeType = NarrativeType.CHAT
    ) -> Tuple[Narrative, bool]:
        """
        Retrieve or create a Narrative

        Workflow:
        1. Generate Query embedding
        2. Vector search
        3. Check similarity threshold
        4. Threshold met -> Return matched Narrative
        5. Threshold not met -> Create new Narrative

        Args:
            query: User query
            user_id: User ID
            agent_id: Agent ID
            narrative_type: Narrative type

        Returns:
            Tuple[Narrative, bool]: (Narrative, is_new)
        """
        logger.info(f"Retrieving Narrative: query='{query[:50]}...'")

        # Generate Query embedding
        query_embedding = await get_embedding(query)
        logger.debug(f"Generated Query embedding (dim={len(query_embedding)})")

        # Search for similar Narratives
        search_results, _ = await self._search(
            query_embedding=query_embedding,
            user_id=user_id,
            agent_id=agent_id,
            top_k=config.NARRATIVE_SEARCH_TOP_K,
            query_text=query  # Needed for EverMemOS mode
        )

        # Enhance scores using recent Events
        if search_results:
            search_results = await self._enhance_with_events(
                search_results=search_results,
                query_embedding=query_embedding
            )

        # Evaluate match results
        if search_results:
            best_match = search_results[0]
            best_score = best_match.similarity_score

            # High confidence match
            if best_score >= config.NARRATIVE_MATCH_HIGH_THRESHOLD:
                logger.info(f"High confidence match: {best_match.narrative_id} (score={best_score:.2f})")
                narrative = await self._crud.load_by_id(best_match.narrative_id)
                if narrative:
                    return narrative, False

            # Low confidence - create new
            elif best_score < config.NARRATIVE_MATCH_LOW_THRESHOLD:
                logger.info(f"Low confidence, creating new topic (score={best_score:.2f})")

            # Middle range - LLM confirmation
            elif config.NARRATIVE_MATCH_USE_LLM:
                candidates = await self._prepare_candidates(search_results[:3])
                llm_result = await self._llm_confirm(query, candidates)
                if llm_result["matched_id"]:
                    narrative = await self._crud.load_by_id(llm_result["matched_id"])
                    if narrative:
                        return narrative, False

            # Middle range but LLM not enabled
            else:
                if best_score >= config.NARRATIVE_MATCH_THRESHOLD:
                    narrative = await self._crud.load_by_id(best_match.narrative_id)
                    if narrative:
                        return narrative, False

        # Create new Narrative
        narrative = await self._create_with_embedding(
            query=query,
            query_embedding=query_embedding,
            user_id=user_id,
            agent_id=agent_id,
            narrative_type=narrative_type
        )
        return narrative, True

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
        1. Generate Query embedding
        2. Vector search Top-K
        3. Enhance scores using recent Events
        4. Two-tier threshold judgment:
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
        await self._ensure_default_narratives(agent_id, user_id)

        # Step 0.5 (P0-4): Query Narratives where user is a PARTICIPANT
        # Replaces the previous _get_narratives_by_entity_jobs(), queries directly via actors
        participant_narratives = await self._get_participant_narratives(
            user_id=user_id,
            agent_id=agent_id
        )
        has_participant_narratives = len(participant_narratives) > 0
        if has_participant_narratives:
            logger.info(f"P0-4: User is a PARTICIPANT in {len(participant_narratives)} Narratives")

        # Step 1: Generate Query embedding
        query_embedding = await get_embedding(query)
        logger.debug(f"Generated Query embedding (dim={len(query_embedding)})")

        # Step 2: Search for similar Narratives
        search_results, retrieval_method = await self._search(
            query_embedding=query_embedding,
            user_id=user_id,
            agent_id=agent_id,
            top_k=max(top_k * 2, config.NARRATIVE_SEARCH_TOP_K),
            query_text=query  # Needed for EverMemOS mode
        )
        logger.info(f"Retrieval method: {retrieval_method}")

        # Step 2.5 (P0-4): Add PARTICIPANT Narratives to candidate list (if not already in search results)
        # This is key: participant_narratives come from Narratives created by other users; vector search won't return them
        existing_narrative_ids = {r.narrative_id for r in search_results}
        for narrative in participant_narratives:
            if narrative.id not in existing_narrative_ids:
                # Calculate similarity score
                if narrative.routing_embedding:
                    score = cosine_similarity(query_embedding, narrative.routing_embedding)
                else:
                    score = 0.5  # Give a medium score when no embedding exists

                # rank will be recalculated after resorting; use 999 as placeholder
                search_results.append(NarrativeSearchResult(
                    narrative_id=narrative.id,
                    similarity_score=score,
                    rank=999
                ))
                logger.info(f"  Added PARTICIPANT Narrative: {narrative.id} (score={score:.2f})")

        # Re-sort (by similarity descending) and update rank
        search_results.sort(key=lambda x: x.similarity_score, reverse=True)
        for i, result in enumerate(search_results):
            result.rank = i + 1

        # Step 3: Enhance scores using recent Events
        # Note: Skip this step in EverMemOS mode, as RRF already combines BM25 + vector search
        # Additional Event enhancement would distort the normalized scores
        if search_results and retrieval_method != "evermemos":
            search_results = await self._enhance_with_events(
                search_results=search_results,
                query_embedding=query_embedding
            )

        # Step 4: Two-tier threshold judgment
        best_score = search_results[0].similarity_score if search_results else None
        # Build per-narrative scores dict (carried through NarrativeSelectionResult
        # so step_1 doesn't need to re-load embeddings and re-compute)
        all_scores = {r.narrative_id: r.similarity_score for r in search_results}

        # Phase 2 & 4: Build evermemos_memories cache (for MemoryModule use)
        # Phase 4: Added episode_contents for short-term memory dedup
        evermemos_memories = {}
        for result in search_results:
            if result.episode_summaries or result.episode_contents:
                narrative = await self._crud.load_by_id(result.narrative_id)
                if narrative:
                    evermemos_memories[result.narrative_id] = {
                        "episode_summaries": result.episode_summaries,
                        "episode_contents": result.episode_contents,  # Phase 4: Raw content
                        "scores": [result.similarity_score],
                        "topic_hint": narrative.topic_hint or "Unknown topic"
                    }
        logger.debug(f"[Phase 2/4] evermemos_memories cache built: {len(evermemos_memories)} Narratives")

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
                query_embedding=query_embedding,
                selection_reason=f"High confidence match, vector similarity {best_score:.2f} >= {config.NARRATIVE_MATCH_HIGH_THRESHOLD}",
                selection_method="high_confidence",
                is_new=False,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
                evermemos_memories=evermemos_memories  # Phase 2: Pass cache
            )

        # P0-4: If user has PARTICIPANT Narratives, force LLM judgment
        if has_participant_narratives:
            logger.info(f"User has PARTICIPANT Narratives, forcing LLM judgment (best_score={f'{best_score:.2f}' if best_score else 'N/A'})")

        # Second tier: Low confidence - LLM unified judgment
        logger.info(f"Low confidence (score={best_score if best_score else 'N/A'}), using LLM unified judgment...")

        if config.NARRATIVE_MATCH_USE_LLM:
            # Call unified LLM judgment (considers search results, default Narratives, and PARTICIPANT Narratives)
            return await self._llm_unified_match(
                query=query,
                search_results=search_results[:3] if search_results else [],
                agent_id=agent_id,
                user_id=user_id,
                top_k=top_k,
                query_embedding=query_embedding,
                narrative_type=narrative_type,
                best_score=best_score,
                participant_narratives=participant_narratives,  # P0-4: Pass PARTICIPANT Narratives
                retrieval_method=retrieval_method  # Pass retrieval method
            )

        # LLM not enabled - Create new Narrative directly
        else:
            logger.info("LLM not enabled, creating new topic directly")
            new_narrative = await self._create_with_embedding(
                query=query,
                query_embedding=query_embedding,
                user_id=user_id,
                agent_id=agent_id,
                narrative_type=narrative_type
            )

            return NarrativeSelectionResult(
                narratives=[new_narrative],
                query_embedding=query_embedding,
                selection_reason="LLM not enabled, created new topic directly",
                selection_method="new_created",
                is_new=True,
                best_score=best_score,
                scores=all_scores,
                retrieval_method=retrieval_method,
                evermemos_memories=evermemos_memories  # Phase 2: Pass cache
            )

    async def retrieve_auxiliary_narratives(
        self,
        query_embedding: List[float],
        user_id: str,
        agent_id: str,
        exclude_narrative_ids: List[str],
        top_k: int
    ) -> List[Narrative]:
        """
        Retrieve auxiliary Narratives (excluding specified Narratives)

        Args:
            query_embedding: Query embedding
            user_id: User ID
            agent_id: Agent ID
            exclude_narrative_ids: List of Narrative IDs to exclude
            top_k: Number of results to return

        Returns:
            List of Narratives
        """
        # Search for similar Narratives
        search_results, _ = await self._search(
            query_embedding=query_embedding,
            user_id=user_id,
            agent_id=agent_id,
            top_k=top_k * 2  # Search more candidates
        )

        # Enhance scores using recent Events
        if search_results:
            search_results = await self._enhance_with_events(
                search_results=search_results,
                query_embedding=query_embedding
            )

        # Load auxiliary Narratives (excluding specified ones)
        narratives = []
        exclude_set = set(exclude_narrative_ids)
        for result in search_results:
            if result.narrative_id not in exclude_set:
                narrative = await self._crud.load_by_id(result.narrative_id)
                if narrative and len(narratives) < top_k:
                    narratives.append(narrative)

        return narratives

    async def retrieve_top_k_by_embedding(
        self,
        query_embedding: List[float],
        user_id: str,
        agent_id: str,
        top_k: int
    ) -> List[NarrativeSearchResult]:
        """
        Retrieve Top-K Narratives by embedding (returns search results, does not load full objects)

        Args:
            query_embedding: Query embedding
            user_id: User ID
            agent_id: Agent ID
            top_k: Number of results to return

        Returns:
            List of NarrativeSearchResult (sorted)
        """
        # Search for similar Narratives
        search_results, _ = await self._search(
            query_embedding=query_embedding,
            user_id=user_id,
            agent_id=agent_id,
            top_k=top_k
        )

        # Enhance scores using recent Events
        if search_results:
            search_results = await self._enhance_with_events(
                search_results=search_results,
                query_embedding=query_embedding
            )

        return search_results[:top_k]

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
            logger.error(
                f"Failed to create default Narratives (agent={agent_id}, user={user_id}): {e}"
            )
            # Do not raise exception, allow continued execution (default Narrative creation failure should not block main flow)

    async def _search(
        self,
        query_embedding: List[float],
        user_id: str,
        agent_id: str,
        top_k: int,
        query_text: str = ""
    ) -> Tuple[List[NarrativeSearchResult], str]:
        """
        Vector search

        Supports two modes:
        1. EverMemOS mode (config.EVERMEMOS_ENABLED=True):
           - Calls EverMemOS HTTP API for semantic retrieval
           - Requires query_text parameter
        2. Native vector retrieval mode (default):
           - Uses local VectorStore for vector similarity search
           - Uses query_embedding parameter

        Args:
            query_embedding: Query embedding vector
            user_id: User ID
            agent_id: Agent ID
            top_k: Number of results to return
            query_text: Query text (needed for EverMemOS mode)

        Returns:
            Tuple[List of NarrativeSearchResult, retrieval method identifier]
            Retrieval method identifier: "evermemos", "vector", "fallback_vector"
        """
        # EverMemOS mode (with fallback)
        # Strategy: Prefer EverMemOS; if it returns empty results, fall back to native vector retrieval
        # This allows normal retrieval during EverMemOS data accumulation period
        if config.EVERMEMOS_ENABLED:
            if not query_text:
                logger.warning("EverMemOS mode requires query_text, falling back to native vector retrieval")
            else:
                try:
                    evermemos = get_evermemos_client(agent_id, user_id)

                    # Query the set of narrative_ids owned by the current agent
                    # Used for Agent isolation of pending_messages (via group_id matching)
                    db_client = await get_db_client()
                    from xyz_agent_context.repository import NarrativeRepository
                    narrative_repo = NarrativeRepository(db_client)
                    agent_narratives = await narrative_repo.get_by_agent(agent_id)
                    agent_narrative_ids = {n.id for n in agent_narratives}

                    results = await evermemos.search_narratives(
                        query=query_text,
                        top_k=top_k,
                        agent_narrative_ids=agent_narrative_ids
                    )
                    if results:
                        logger.info(f"[EverMemOS] Retrieval successful: {len(results)} candidate Narratives")
                        return results, "evermemos"
                    else:
                        # EverMemOS returned empty results, data may still be processing
                        logger.info("[EverMemOS] Returned 0 results, falling back to native vector retrieval (data may still be processing)")
                except Exception as e:
                    logger.error(f"EverMemOS retrieval failed, falling back to native vector retrieval: {e}")

        # Native vector retrieval mode (default / fallback)
        db_client = await get_db_client()

        filters = {"user_id": user_id, "agent_id": agent_id}
        results = await self._vector_store.search(
            query_embedding=query_embedding,
            filters=filters,
            top_k=top_k,
            min_score=config.VECTOR_SEARCH_MIN_SCORE,
            db_client=db_client
        )

        # Determine if this is fallback or default vector retrieval
        retrieval_method = "fallback_vector" if config.EVERMEMOS_ENABLED else "vector"
        logger.debug(f"[Native vector retrieval] Found {len(results)} candidate Narratives (method={retrieval_method})")
        return results, retrieval_method

    async def _enhance_with_events(
        self,
        search_results: List[NarrativeSearchResult],
        query_embedding: List[float]
    ) -> List[NarrativeSearchResult]:
        """Enhance scores using recent Events"""
        weight = config.RECENT_EVENTS_WEIGHT
        max_events = config.MATCH_RECENT_EVENTS_COUNT

        enhanced_results = []

        for result in search_results:
            narrative_id = result.narrative_id
            topic_score = result.similarity_score

            try:
                narrative = await self._crud.load_by_id(narrative_id)

                if narrative and narrative.event_ids and self._event_service:
                    recent_event_ids = narrative.event_ids[-max_events:]
                    events = await self._event_service.load_events_from_db(recent_event_ids)

                    # Collect query texts and generate embeddings
                    event_embeddings = []
                    for event in events:
                        if event and event.env_context:
                            input_text = event.env_context.get("input", "")
                            if input_text:
                                try:
                                    emb = await get_embedding(input_text)
                                    event_embeddings.append(emb)
                                except Exception:
                                    pass

                    # Calculate enhanced score
                    if event_embeddings:
                        avg_embedding = compute_average_embedding(event_embeddings)
                        events_score = cosine_similarity(query_embedding, avg_embedding)
                        final_score = topic_score * (1 - weight) + events_score * weight

                        enhanced_results.append(NarrativeSearchResult(
                            narrative_id=narrative_id,
                            similarity_score=final_score,
                            rank=0
                        ))
                        continue

                enhanced_results.append(result)

            except Exception as e:
                logger.debug(f"Enhancement failed for {narrative_id}: {e}")
                enhanced_results.append(result)

        # Re-sort
        enhanced_results.sort(key=lambda x: x.similarity_score, reverse=True)
        for i, result in enumerate(enhanced_results):
            result.rank = i + 1

        return enhanced_results



    async def _llm_unified_match(
        self,
        query: str,
        search_results: List[NarrativeSearchResult],
        agent_id: str,
        user_id: str,
        top_k: int,
        query_embedding: List[float],
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
            search_results: Vector search results
            agent_id: Agent ID
            user_id: User ID
            top_k: Number of results to return
            query_embedding: Query embedding vector
            narrative_type: Narrative type
            best_score: Best match score
            participant_narratives: P0-4 - Narratives where user is a PARTICIPANT

        Returns:
            NarrativeSelectionResult
        """
        # 1. Prepare search result candidates
        # Phase 1: Added matched_content field (from EverMemOS episode_summaries)
        # Per-narrative scores from search (passed through to NarrativeSelectionResult)
        all_scores = {r.narrative_id: r.similarity_score for r in search_results}

        # Phase 2 & 4: Build evermemos_memories cache for MemoryModule use
        search_candidates = []
        evermemos_memories = {}  # Phase 2: Cache EverMemOS retrieval results

        for result in search_results:
            narrative = await self._crud.load_by_id(result.narrative_id)
            if narrative:
                # Phase 1: Use episode_summaries as matched_content
                matched_content = ""
                # Phase 1 debug log
                logger.debug(
                    f"[Phase 1] NarrativeSearchResult {result.narrative_id}: "
                    f"episode_summaries count={len(result.episode_summaries)}, "
                    f"episode_contents count={len(result.episode_contents)}"
                )
                if result.episode_summaries or result.episode_contents:
                    # Merge summaries, separated by newlines, max 500 characters
                    if result.episode_summaries:
                        matched_content = "\n".join(result.episode_summaries)
                        if len(matched_content) > 500:
                            matched_content = matched_content[:500] + "..."

                    # Phase 2 & 4: Cache to evermemos_memories for MemoryModule use
                    evermemos_memories[result.narrative_id] = {
                        "episode_summaries": result.episode_summaries,
                        "episode_contents": result.episode_contents,  # Phase 4: Raw content
                        "scores": [result.similarity_score],  # Extensible to per-episode scores
                        "topic_hint": narrative.topic_hint or "Unknown topic"
                    }

                # Use narrative_info (LLM-generated) for richer candidate info.
                # Falls back to topic_hint only when narrative_info is empty.
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
                    "matched_content": matched_content  # Phase 1: New field
                })

        logger.debug(f"[Phase 2/4] evermemos_memories cache: {len(evermemos_memories)} Narratives")

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
                    query_embedding=query_embedding,
                    selection_reason=f"LLM matched default Narrative: {reason}",
                    selection_method="default_narrative_matched",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    evermemos_memories=evermemos_memories  # Phase 2: Pass cache
                )

            elif matched_type == "participant":
                # P0-4: Matched a PARTICIPANT Narrative (task priority)
                logger.info(f"LLM matched PARTICIPANT Narrative: {matched_id}")
                matched_narrative = await self._crud.load_by_id(matched_id)

                return NarrativeSelectionResult(
                    narratives=[matched_narrative] if matched_narrative else [],
                    query_embedding=query_embedding,
                    selection_reason=f"LLM matched PARTICIPANT Narrative: {reason}",
                    selection_method="participant_narrative_matched",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    evermemos_memories=evermemos_memories  # Phase 2: Pass cache
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
                    query_embedding=query_embedding,
                    selection_reason=f"LLM matched search result: {reason}",
                    selection_method="llm_confirmed",
                    is_new=False,
                    best_score=best_score,
                    scores=all_scores,
                    retrieval_method=retrieval_method,
                    evermemos_memories=evermemos_memories  # Phase 2: Pass cache
                )

        # 5. No match, create new Narrative
        logger.info("LLM determined no match with any Narrative, creating new topic")
        new_narrative = await self._create_with_embedding(
            query=query,
            query_embedding=query_embedding,
            user_id=user_id,
            agent_id=agent_id,
            narrative_type=narrative_type
        )

        return NarrativeSelectionResult(
            narratives=[new_narrative],
            query_embedding=query_embedding,
            selection_reason=f"LLM determined new topic: {llm_result.get('reason', 'No match')}",
            selection_method="new_created",
            is_new=True,
            best_score=best_score,
            scores=all_scores,
            retrieval_method=retrieval_method,
            evermemos_memories=evermemos_memories  # Phase 2: Pass cache
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
            logger.error(f"PARTICIPANT Narratives: Query failed: {e}")
            return []

    async def _create_with_embedding(
        self,
        query: str,
        query_embedding: List[float],
        user_id: str,
        agent_id: str,
        narrative_type: NarrativeType
    ) -> Narrative:
        """Create a Narrative with embedding"""
        # Extract keywords
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

        # Set routing index fields
        from datetime import datetime, timezone
        narrative.topic_keywords = topic_keywords
        narrative.topic_hint = topic_hint
        narrative.routing_embedding = query_embedding
        narrative.embedding_updated_at = datetime.now(timezone.utc)
        narrative.events_since_last_embedding_update = 0

        # Save
        await self._crud.save(narrative)

        # Add to VectorStore
        await self._vector_store.add(
            narrative_id=narrative.id,
            embedding=query_embedding,
            metadata={"user_id": user_id, "agent_id": agent_id}
        )

        # Dual-write to embeddings_store
        from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import store_embedding
        await store_embedding("narrative", narrative.id, query_embedding, source_text=topic_hint)

        logger.info(f"Created new Narrative: {narrative.id}")
        return narrative
