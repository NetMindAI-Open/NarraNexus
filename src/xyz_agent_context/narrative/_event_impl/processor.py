"""
Event processing implementation

@file_name: processor.py
@author: NetMind.AI
@date: 2025-12-22
@description: Event processing, embedding generation, context selection
"""

from __future__ import annotations

import json
from typing import List, Optional, TYPE_CHECKING

from loguru import logger

from ..config import config
from ..models import Event, EventLogEntry
from .crud import EventCRUD

# Use common utilities from utils
from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding, cosine_similarity

if TYPE_CHECKING:
    from xyz_agent_context.schema.module_schema import ModuleInstance
    from xyz_agent_context.utils.database import AsyncDatabaseClient


class EventProcessor:
    """
    Event Processor

    Responsibilities:
    - Update Event data (final_output, event_log, etc.)
    - Generate Event embedding
    - Select Events for context inclusion
    """

    def __init__(self, agent_id: str):
        """
        Initialize processor

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._crud = EventCRUD(agent_id)

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._crud.set_database_client(db_client)

    async def update_event(
        self,
        event_id: str,
        final_output: Optional[str] = None,
        event_log: Optional[List[EventLogEntry]] = None,
        module_instances: Optional[List["ModuleInstance"]] = None,
        generate_embedding: bool = True,
    ) -> int:
        """
        Update an Event

        Args:
            event_id: Event ID
            final_output: Final output
            event_log: Event log
            module_instances: Module instances
            generate_embedding: Whether to generate embedding

        Returns:
            Number of affected rows
        """
        update_data = {}

        if final_output is not None:
            update_data["final_output"] = final_output

            # Generate embedding
            if generate_embedding:
                current_event = await self._crud.load_by_id(event_id)
                if current_event:
                    input_content = current_event.env_context.get("input", "")
                    embedding, embedding_text = await self._generate_embedding(
                        input_content, final_output
                    )
                    if embedding:
                        update_data["event_embedding"] = json.dumps(embedding)
                        update_data["embedding_text"] = embedding_text
                        # Dual-write to embeddings_store
                        from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import store_embedding
                        await store_embedding("event", event_id, embedding, source_text=embedding_text)

        if event_log is not None:
            update_data["event_log"] = json.dumps([log.model_dump(mode='json') for log in event_log])

        if module_instances is not None:
            update_data["module_instances"] = json.dumps([m.model_dump(mode='json') for m in module_instances])

        if not update_data:
            return 0

        return await self._crud.update(event_id, update_data)

    async def _generate_embedding(
        self,
        input_content: str,
        final_output: str,
        max_text_length: Optional[int] = None
    ) -> tuple[Optional[List[float]], str]:
        """
        Generate Event embedding

        Strategy:
        1. Combine input + output
        2. Truncate to reasonable length
        3. Call embedding API

        Args:
            input_content: User input
            final_output: Agent output
            max_text_length: Maximum text length

        Returns:
            (embedding, embedding_text)
        """
        max_text_length = max_text_length or config.EVENT_EMBEDDING_MAX_TEXT_LENGTH

        # Combine text
        embedding_text = ""

        if input_content:
            embedding_text += input_content[:max_text_length // 2]

        if final_output:
            remaining_length = max_text_length - len(embedding_text)
            if remaining_length > 50:
                embedding_text += " " + final_output[:remaining_length]

        embedding_text = embedding_text.strip()

        if not embedding_text:
            return None, ""

        try:
            embedding = await get_embedding(embedding_text)
            logger.debug(f"Generated Event embedding (dim={len(embedding)})")
            return embedding, embedding_text
        except Exception as e:
            logger.warning(f"Failed to generate Event embedding: {e}")
            return None, embedding_text

    async def select_for_context(
        self,
        narrative_event_ids: List[str],
        query_embedding: Optional[List[float]] = None,
        max_recent: Optional[int] = None,
        max_relevant: Optional[int] = None,
        max_total: Optional[int] = None,
        min_relevance_score: Optional[float] = None,
    ) -> List[Event]:
        """
        Mixed strategy for selecting Events to include in Context

        Strategy:
        1. Most recent N Events (ensures conversation continuity)
        2. Relevance Top-K Events (ensures Query relevance)
        3. Merge with deduplication, sort by time

        Args:
            narrative_event_ids: All Event IDs associated with the Narrative
            query_embedding: Query embedding (for relevance calculation)
            max_recent: Most recent N
            max_relevant: Relevance Top-K
            max_total: Maximum number of results to return
            min_relevance_score: Minimum relevance threshold

        Returns:
            List of selected Events (sorted by time)
        """
        # Use config defaults
        max_recent = max_recent or config.MAX_RECENT_EVENTS
        max_relevant = max_relevant or config.MAX_RELEVANT_EVENTS
        max_total = max_total or config.MAX_EVENTS_IN_CONTEXT
        min_relevance_score = min_relevance_score or config.EVENT_RELEVANCE_MIN_SCORE

        if not narrative_event_ids:
            return []

        # Get most recent N
        recent_event_ids = narrative_event_ids[-max_recent:] if len(narrative_event_ids) > max_recent else narrative_event_ids

        # Load all Events
        all_events = await self._crud.load_by_ids(narrative_event_ids)
        events_by_id = {e.id: e for e in all_events if e is not None}

        # Select Top-K based on relevance
        relevant_event_ids = []
        if query_embedding and len(narrative_event_ids) > max_recent:
            from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import (
                use_embedding_store,
                get_stored_embeddings_batch,
            )
            all_event_ids = list(events_by_id.keys())
            new_system = use_embedding_store()
            store_vectors: dict = {}
            if new_system:
                store_vectors = await get_stored_embeddings_batch("event", all_event_ids)

            event_scores = []
            for event_id, event in events_by_id.items():
                if new_system:
                    vector = store_vectors.get(event_id)
                else:
                    vector = event.event_embedding
                if vector:
                    score = cosine_similarity(query_embedding, vector)
                    if score >= min_relevance_score:
                        event_scores.append((event_id, score))

            event_scores.sort(key=lambda x: x[1], reverse=True)
            relevant_event_ids = [eid for eid, _ in event_scores[:max_relevant]]

        # Merge with deduplication
        selected_ids = []
        seen = set()

        for eid in relevant_event_ids:
            if eid not in seen:
                selected_ids.append(eid)
                seen.add(eid)

        for eid in recent_event_ids:
            if eid not in seen:
                selected_ids.append(eid)
                seen.add(eid)

        # Truncate
        if len(selected_ids) > max_total:
            selected_ids = selected_ids[:max_total]

        # Sort by original order
        id_order = {eid: i for i, eid in enumerate(narrative_event_ids)}
        selected_ids.sort(key=lambda eid: id_order.get(eid, float('inf')))

        # Build return list
        selected_events = [events_by_id[eid] for eid in selected_ids if eid in events_by_id]

        logger.info(f"Selected {len(selected_events)} Events")
        return selected_events
