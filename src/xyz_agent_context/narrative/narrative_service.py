"""
@file_name: narrative_service.py
@author: NetMind.AI
@date: 2025-12-22
@description: Narrative service protocol layer

This is the public interface for NarrativeService; all concrete implementations are delegated to the _narrative_impl module.

Features:
1. select() - Select/create Narrative
2. update_with_event() - Update Narrative with an Event
3. CRUD operations
4. Instance management
5. Prompt generation
"""

from __future__ import annotations

from typing import List, Optional, Tuple, TYPE_CHECKING

from loguru import logger

from .models import (
    ConversationSession,
    Event,
    Narrative,
    NarrativeActor,
    NarrativeSelectionResult,
    NarrativeType,
)
from ._narrative_impl import (
    NarrativeCRUD,
    NarrativeRetrieval as _NarrativeRetrieval,
    NarrativeUpdater as _NarrativeUpdater,
    InstanceHandler,
    PromptBuilder,
    ContinuityDetector,
)

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient
    from xyz_agent_context.schema.module_schema import InstanceStatus


class NarrativeService:
    """
    Narrative Unified Service - Main interface for AgentRuntime

    This is a protocol layer; all concrete implementations are delegated to the _narrative_impl module.

    Main features:
    1. select() - Select the appropriate Narrative
    2. update_with_event() - Update Narrative with an Event
    3. CRUD operations
    4. Instance management
    5. Prompt generation

    Usage:
        >>> service = NarrativeService(agent_id="agent_1")
        >>> narratives, embedding = await service.select(agent_id, user_id, input_content)
        >>> await service.update_with_event(narrative, event)
    """

    def __init__(
        self,
        agent_id: str,
        database_client: Optional["AsyncDatabaseClient"] = None
    ):
        """
        Initialize Narrative Service

        Args:
            agent_id: Agent ID
            database_client: Database client (optional)
        """
        self.agent_id = agent_id
        self._database_client = database_client

        # Implementation modules
        self._crud = NarrativeCRUD(agent_id)
        self._retrieval = _NarrativeRetrieval(agent_id)
        self._updater = _NarrativeUpdater(agent_id)
        self._instance_handler = InstanceHandler(agent_id)

        # Shared vector_store
        self._updater.set_vector_store(self._retrieval.vector_store)

        # Session and Continuity (lazy loaded)
        self._session_service = None
        self._continuity_detector = None

        logger.info(f"NarrativeService initialized (agent_id={agent_id})")

    # =========================================================================
    # Dependency Injection
    # =========================================================================

    def set_event_service(self, event_service):
        """Inject EventService"""
        self._retrieval.set_event_service(event_service)
        self._updater.set_event_service(event_service)

    @property
    def database_client(self) -> Optional["AsyncDatabaseClient"]:
        """Get the database client"""
        return self._database_client

    # =========================================================================
    # Main Feature: select()
    # =========================================================================

    async def select(
        self,
        agent_id: str,
        user_id: str,
        input_content: str,
        max_narratives: Optional[int] = None,
        session: Optional[ConversationSession] = None,
        awareness: Optional[str] = None,
    ) -> NarrativeSelectionResult:
        """
        Select the appropriate Narratives

        Workflow:
        1. Detect topic continuity
        2. Vector match or create new Narrative
        3. Retrieve auxiliary Narratives
        4. Return results

        Args:
            agent_id: Agent ID
            user_id: User ID
            input_content: User input
            max_narratives: Maximum return count
            session: Session object
            awareness: Agent self-awareness content (optional)

        Returns:
            NarrativeSelectionResult: Contains Narrative list, selection reason, and other complete info
        """
        from .config import config
        from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding

        max_narratives = max_narratives or config.MAX_NARRATIVES_IN_CONTEXT
        logger.info("NarrativeService.select() started")

        # Continuity detection
        is_continuous = False
        continuity_reason = ""
        if session and session.last_query:
            try:
                detector = self._get_continuity_detector()
                if detector:
                    # Get the current Narrative (if any)
                    current_narrative = None
                    if session.current_narrative_id:
                        current_narrative = await self._crud.load_by_id(session.current_narrative_id)

                    result = await detector.detect(
                        current_query=input_content,
                        session=session,
                        current_narrative=current_narrative,
                        awareness=awareness
                    )
                    logger.debug(f"Continuity detection reason: {result.reason}")
                    is_continuous = result.is_continuous
                    continuity_reason = result.reason
            except Exception as e:
                logger.warning(f"Continuity detection failed: {e}")

        # Generate query embedding
        query_embedding = None
        try:
            query_embedding = await get_embedding(input_content)
        except Exception:
            pass

        narratives: List[Narrative] = []
        selection_reason = ""
        selection_method = ""
        retrieval_method = ""  # Retrieval method identifier

        if is_continuous and session and session.current_narrative_id:
            # Continuity detection is True: main Narrative is the current one, but still need to retrieve Top-K Narratives
            # This allows including conversation history from other related Narratives
            main_narrative = await self._crud.load_by_id(session.current_narrative_id)
            if main_narrative:
                logger.info(f"Continuity detection passed, main Narrative: {main_narrative.id}")
                selection_reason = f"Topic continuity detection passed: {continuity_reason}"
                selection_method = "continuous"
                retrieval_method = "session"  # Continuity detection, obtained from session, no vector retrieval needed

                # Retrieve Top-K Narratives (don't exclude main Narrative, let it participate in ranking naturally)
                if query_embedding:
                    # Retrieve Top-K+1 candidates (since main Narrative may not be in Top-K)
                    search_results = await self._retrieval.retrieve_top_k_by_embedding(
                        query_embedding=query_embedding,
                        user_id=user_id,
                        agent_id=agent_id,
                        top_k=max_narratives + 1
                    )

                    if search_results:
                        # Load Narratives (prioritize including main Narrative)
                        main_found = False
                        for result in search_results:
                            narrative = await self._crud.load_by_id(result.narrative_id)
                            if narrative:
                                if narrative.id == main_narrative.id:
                                    # Main Narrative is in results, mark and add to first position
                                    if not main_found:
                                        narratives.insert(0, narrative)
                                        main_found = True
                                elif len(narratives) < max_narratives:
                                    narratives.append(narrative)

                        # If main Narrative is not in search results, force add to first position
                        if not main_found:
                            narratives.insert(0, main_narrative)
                            # If exceeds max_narratives, remove the last one
                            if len(narratives) > max_narratives:
                                narratives = narratives[:max_narratives]
                    else:
                        # No search results, return only main Narrative
                        narratives = [main_narrative]
                else:
                    # No query_embedding, return only main Narrative
                    narratives = [main_narrative]

                logger.info(f"Continuity detection: returning {len(narratives)} Narratives (main Narrative in first position)")

        # Phase 2: Cache EverMemOS retrieval results for MemoryModule use
        evermemos_memories = {}

        if not narratives:
            # Not continuous or continuity detection failed: retrieve Top-K
            retrieval_result = await self._retrieval.retrieve_top_k(
                query=input_content,
                user_id=user_id,
                agent_id=agent_id,
                top_k=max_narratives
            )
            narratives = retrieval_result.narratives
            query_embedding = retrieval_result.query_embedding
            selection_reason = retrieval_result.selection_reason
            selection_method = retrieval_result.selection_method
            retrieval_method = retrieval_result.retrieval_method
            evermemos_memories = retrieval_result.evermemos_memories  # Phase 2: Extract EverMemOS cache

        # Update Session (using main Narrative)
        if session and narratives:
            from datetime import datetime, timezone
            session.last_query = input_content
            session.last_query_embedding = query_embedding
            session.current_narrative_id = narratives[0].id
            session.query_count += 1
            session.last_query_time = datetime.now(timezone.utc)  # Update query time

        logger.info(f"select() completed: returning {len(narratives)} Narratives, method={selection_method}")

        return NarrativeSelectionResult(
            narratives=narratives,
            query_embedding=query_embedding,
            selection_reason=selection_reason,
            selection_method=selection_method,
            is_new=(selection_method == "new_created"),
            best_score=None,
            retrieval_method=retrieval_method,
            evermemos_memories=evermemos_memories  # Phase 2: Pass EverMemOS cache
        )

    # =========================================================================
    # Update Features
    # =========================================================================

    async def update_with_event(
        self,
        narrative: Narrative,
        event: Event,
        is_main_narrative: bool = True,
        is_default_narrative: bool = False
    ) -> Narrative:
        """
        Update Narrative with an Event

        Args:
            narrative: Narrative object
            event: Event object
            is_main_narrative: Whether this is the main Narrative
                - True: Full update (LLM dynamic update + Embedding update)
                - False: Basic update only (associate Event, update dynamic_summary)
            is_default_narrative: Whether this is a default Narrative (is_special="default")
                - True: Only add event_id, no other updates
                - False: Normal update
        """
        return await self._updater.update_with_event(
            narrative,
            event,
            is_main_narrative=is_main_narrative,
            is_default_narrative=is_default_narrative
        )

    async def check_and_update_embedding(self, narrative: Narrative) -> bool:
        """Check and update embedding"""
        return await self._updater.check_and_update_embedding(narrative)

    async def force_update_embedding(self, narrative: Narrative):
        """Force update embedding"""
        await self._updater.force_update_embedding(narrative)

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    async def load_narrative_from_db(self, narrative_id: str) -> Optional[Narrative]:
        """Load a Narrative from the database"""
        return await self._crud.load_by_id(narrative_id)

    async def save_narrative_to_db(self, narrative: Narrative) -> int:
        """Save a Narrative to the database"""
        return await self._crud.save(narrative)

    async def load_narratives_by_agent_user(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10
    ) -> List[Narrative]:
        """Load Narratives by Agent and User"""
        return await self._crud.load_by_agent_user(agent_id, user_id, limit)

    async def create_narrative(
        self,
        agent_id: str,
        user_id: str,
        narrative_type: NarrativeType = NarrativeType.CHAT,
        title: str = "New Narrative",
        description: str = "",
        actors: Optional[List[NarrativeActor]] = None,
        save_to_db: bool = True,
    ) -> Narrative:
        """Create a new Narrative"""
        return await self._crud.create(
            agent_id=agent_id,
            user_id=user_id,
            narrative_type=narrative_type,
            title=title,
            description=description,
            actors=actors,
            save_to_db=save_to_db
        )

    # =========================================================================
    # Instance Management
    # =========================================================================

    async def handle_instance_completion(
        self,
        narrative_id: str,
        instance_id: str,
        new_status: "InstanceStatus",
        narrative: Optional[Narrative] = None,
        save_to_db: bool = True
    ) -> List[str]:
        """Handle Instance completion event"""
        return await self._instance_handler.handle_completion(
            narrative_id=narrative_id,
            instance_id=instance_id,
            new_status=new_status,
            narrative=narrative,
            save_to_db=save_to_db
        )

    # =========================================================================
    # Prompt Generation
    # =========================================================================

    async def combine_main_narrative_prompt(self, narrative: Narrative) -> str:
        """Generate the main Prompt for a Narrative"""
        return await PromptBuilder.build_main_prompt(narrative)

    # =========================================================================
    # Retrieval Features
    # =========================================================================

    async def retrieve_or_create(
        self,
        query: str,
        user_id: str,
        agent_id: str,
        narrative_type: NarrativeType = NarrativeType.CHAT
    ) -> Tuple[Narrative, bool]:
        """Retrieve or create a Narrative"""
        return await self._retrieval.retrieve_or_create(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            narrative_type=narrative_type
        )

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _get_continuity_detector(self) -> Optional[ContinuityDetector]:
        """Get the continuity detector (lazy loaded)"""
        if self._continuity_detector is None:
            try:
                self._continuity_detector = ContinuityDetector()
            except Exception as e:
                logger.warning(f"ContinuityDetector initialization failed: {e}")
        return self._continuity_detector
