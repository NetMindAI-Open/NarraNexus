"""
Event processing implementation

@file_name: processor.py
@author: NetMind.AI
@date: 2025-12-22
@description: Event processing and context selection
"""

from __future__ import annotations

import json
from typing import List, Optional, TYPE_CHECKING

from loguru import logger

from ..config import config
from ..models import Event, EventLogEntry
from .crud import EventCRUD

if TYPE_CHECKING:
    from xyz_agent_context.schema.module_schema import ModuleInstance
    from xyz_agent_context.utils.database import AsyncDatabaseClient


class EventProcessor:
    """
    Event Processor

    Responsibilities:
    - Update Event data (final_output, event_log, etc.)
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
    ) -> int:
        """
        Update an Event

        Args:
            event_id: Event ID
            final_output: Final output
            event_log: Event log
            module_instances: Module instances

        Returns:
            Number of affected rows
        """
        update_data = {}

        if final_output is not None:
            update_data["final_output"] = final_output
            # Event embeddings retired — event_embedding/embedding_text columns
            # are inert tombstones; event selection is recency-based (BM25 lives
            # in the unified MemoryEngine, not here).

        if event_log is not None:
            update_data["event_log"] = json.dumps([log.model_dump(mode='json') for log in event_log])

        if module_instances is not None:
            update_data["module_instances"] = json.dumps([m.model_dump(mode='json') for m in module_instances])

        if not update_data:
            return 0

        return await self._crud.update(event_id, update_data)

    async def select_for_context(
        self,
        narrative_event_ids: List[str],
        max_recent: Optional[int] = None,
        max_total: Optional[int] = None,
    ) -> List[Event]:
        """
        Select Events to include in Context (most-recent-N, truncated).

        Embedding-based relevance selection is retired — cross-narrative
        semantic recall lives in the unified MemoryEngine (BM25). This returns
        the narrative's most recent events in original order.

        Args:
            narrative_event_ids: All Event IDs associated with the Narrative
            max_recent: Most recent N to keep
            max_total: Maximum number of results to return

        Returns:
            List of selected Events (in original order)
        """
        # Use config defaults
        max_recent = max_recent or config.MAX_RECENT_EVENTS
        max_total = max_total or config.MAX_EVENTS_IN_CONTEXT

        if not narrative_event_ids:
            return []

        # Get most recent N
        recent_event_ids = narrative_event_ids[-max_recent:] if len(narrative_event_ids) > max_recent else narrative_event_ids

        # Load Events
        all_events = await self._crud.load_by_ids(narrative_event_ids)
        events_by_id = {e.id: e for e in all_events if e is not None}

        # Deduplicate (preserve order)
        selected_ids = []
        seen = set()
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
