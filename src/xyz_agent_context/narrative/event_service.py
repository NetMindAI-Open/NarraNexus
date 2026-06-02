"""
@file_name: event_service.py
@author: NetMind.AI
@date: 2025-12-22
@description: Event service protocol layer

This is the public interface for EventService; all concrete implementations are delegated to the _event_impl module.

Features:
1. create_event() - Create an Event
2. update_event_in_db() - Update an Event
3. load_event_from_db() - Load an Event
4. select_events_for_context() - Select Events to add to context
5. Prompt generation
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING, Union

from loguru import logger

from .models import Event, EventLogEntry, TriggerType
from ._event_impl import (
    EventCRUD,
    EventProcessor,
    EventPromptBuilder,
)

if TYPE_CHECKING:
    from xyz_agent_context.schema.module_schema import ModuleInstance
    from xyz_agent_context.utils import DatabaseClient, AsyncDatabaseClient
    from xyz_agent_context.repository import EventRepository
    from xyz_agent_context.utils import DataLoader

# Database client type
DatabaseClientType = Union["DatabaseClient", "AsyncDatabaseClient"]


class EventService:
    """
    Event Service - Manages Event creation, update, and deletion

    This is a protocol layer; all concrete implementations are delegated to the _event_impl module.

    Usage:
        >>> service = EventService(agent_id)
        >>> event = await service.create_event(agent_id, user_id, input_content)
        >>> await service.update_event_in_db(event.id, final_output="...")
    """

    def __init__(
        self,
        agent_id: str,
        database_client: Optional[DatabaseClientType] = None,
        event_repository: Optional["EventRepository"] = None,
        event_loader: Optional["DataLoader[str, Event]"] = None,
    ):
        """
        Initialize EventService

        Args:
            agent_id: Agent ID
            database_client: Database client
            event_repository: Event repository (optional)
            event_loader: DataLoader (optional)
        """
        self.agent_id = agent_id
        self._database_client = database_client
        self.events = []

        # Implementation modules
        self._crud = EventCRUD(agent_id)
        self._processor = EventProcessor(agent_id)

        # Set dependencies
        if event_repository:
            self._crud.set_repository(event_repository)
        if event_loader:
            self._crud.set_loader(event_loader)

        logger.debug(f"EventService initialized (agent_id={agent_id})")

    @property
    def database_client(self) -> Optional[DatabaseClientType]:
        """Get the database client"""
        return self._database_client

    # =========================================================================
    # Create Event
    # =========================================================================

    async def create_event(
        self,
        agent_id: str,
        user_id: str,
        input_content: str,
        retrieval_anchor: Optional[str] = None,
    ) -> Event:
        """
        Create an Event and save to database

        Args:
            agent_id: Agent ID
            user_id: User ID
            input_content: Input content

        Returns:
            Newly created Event
        """
        logger.debug(f"create_event: agent={agent_id}, user={user_id}")
        return await self._crud.create(
            agent_id=agent_id,
            user_id=user_id,
            input_content=input_content,
            trigger_type=TriggerType.CHAT,
            save_to_db=True,
            retrieval_anchor=retrieval_anchor,
        )

    # =========================================================================
    # Update Event
    # =========================================================================

    async def update_event_narrative_id(self, event_id: str, narrative_id: str) -> int:
        """Update the narrative_id of an Event"""
        return await self._crud.update_narrative_id(event_id, narrative_id)

    async def update_event_in_db(
        self,
        event_id: str,
        final_output: Optional[str] = None,
        event_log: Optional[List[EventLogEntry]] = None,
        module_instances: Optional[List["ModuleInstance"]] = None,
        generate_embedding: bool = True,
    ) -> int:
        """
        Update an Event in the database

        Args:
            event_id: Event ID
            final_output: Final output
            event_log: Event log
            module_instances: Module instances
            generate_embedding: Whether to generate embedding

        Returns:
            Number of affected rows
        """
        return await self._processor.update_event(
            event_id=event_id,
            final_output=final_output,
            event_log=event_log,
            module_instances=module_instances,
            generate_embedding=generate_embedding
        )

    async def duplicate_event_for_narrative(
        self,
        original_event: Event,
        narrative_id: str
    ) -> Event:
        """Duplicate an Event for associating with a different Narrative"""
        return await self._crud.duplicate(original_event, narrative_id)

    # =========================================================================
    # Load Event
    # =========================================================================

    async def load_event_from_db(self, event_id: str) -> Optional[Event]:
        """Load an Event from the database"""
        return await self._crud.load_by_id(event_id)

    async def load_events_from_db(self, event_ids: List[str]) -> List[Optional[Event]]:
        """Batch load Events (solves the N+1 problem)"""
        return await self._crud.load_by_ids(event_ids)

    # =========================================================================
    # Context Selection
    # =========================================================================

    async def select_events_for_context(
        self,
        narrative_event_ids: List[str],
        query_embedding: Optional[List[float]] = None,
        max_recent: Optional[int] = None,
        max_relevant: Optional[int] = None,
        max_total: Optional[int] = None,
        min_relevance_score: Optional[float] = None,
    ) -> List[Event]:
        """
        Hybrid strategy for selecting Events to add to Context

        Strategy:
        1. Most recent N Events (ensure conversation continuity)
        2. Relevance Top-K Events (ensure Query relevance)
        3. Merge, deduplicate, and sort by time

        Args:
            narrative_event_ids: Event IDs associated with the Narrative
            query_embedding: Query embedding
            max_recent: Most recent N
            max_relevant: Relevance Top-K
            max_total: Maximum return count
            min_relevance_score: Minimum relevance threshold

        Returns:
            List of selected Events
        """
        return await self._processor.select_for_context(
            narrative_event_ids=narrative_event_ids,
            query_embedding=query_embedding,
            max_recent=max_recent,
            max_relevant=max_relevant,
            max_total=max_total,
            min_relevance_score=min_relevance_score
        )

    # =========================================================================
    # Prompt Generation
    # =========================================================================

    @staticmethod
    async def get_event_head_tail_prompt() -> Dict[str, str]:
        """Generate the head and tail sections of the Event Prompt"""
        return await EventPromptBuilder.get_head_tail()

    @staticmethod
    async def combine_event_prompt(event: Event, order: str) -> str:
        """Generate the detailed Prompt for a single Event"""
        return await EventPromptBuilder.build_single(event, order)
