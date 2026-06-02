"""
Event CRUD operations implementation

@file_name: crud.py
@author: NetMind.AI
@date: 2025-12-22
@description: Event creation, read, update operations
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from uuid import uuid4

from loguru import logger

from ..models import Event, EventLogEntry, TriggerType

if TYPE_CHECKING:
    from xyz_agent_context.schema.module_schema import ModuleInstance
    from xyz_agent_context.utils.database import AsyncDatabaseClient
    from xyz_agent_context.repository import EventRepository
    from xyz_agent_context.utils import DataLoader


class EventCRUD:
    """
    Event CRUD operations

    Responsibilities:
    - Create Event
    - Load Event from database (single and batch)
    - Save/update Event to database
    """

    def __init__(self, agent_id: str):
        """
        Initialize CRUD operations

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._database_client: Optional["AsyncDatabaseClient"] = None
        self._repository: Optional["EventRepository"] = None
        self._loader: Optional["DataLoader"] = None

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._database_client = db_client

    def set_repository(self, repository: "EventRepository"):
        """Set the Repository"""
        self._repository = repository

    def set_loader(self, loader: "DataLoader"):
        """Set the DataLoader"""
        self._loader = loader

    async def _get_db_client(self) -> "AsyncDatabaseClient":
        """Get the database client (lazy loaded)"""
        if self._database_client is None:
            from xyz_agent_context.utils.db_factory import get_db_client
            self._database_client = await get_db_client()
        return self._database_client

    async def create(
        self,
        agent_id: str,
        user_id: str,
        input_content: str,
        trigger_type: TriggerType = TriggerType.CHAT,
        narrative_id: Optional[str] = None,
        save_to_db: bool = True,
        retrieval_anchor: Optional[str] = None,
    ) -> Event:
        """
        Create an Event

        Args:
            agent_id: Agent ID
            user_id: User ID
            input_content: Input content
            trigger_type: Trigger type
            narrative_id: Narrative ID
            save_to_db: Whether to save to database

        Returns:
            Newly created Event
        """
        now = datetime.now(timezone.utc)
        event_id = f"evt_{uuid4().hex[:16]}"

        event = Event(
            id=event_id,
            trigger=trigger_type,
            trigger_source=user_id,
            env_context={
                "input": input_content,
                # Clean retrieval anchor for event-vector embedding (write-side),
                # kept alongside the raw input. Falls back to input when no
                # trigger anchor was supplied. See 2026-06-01 design doc.
                "anchor": retrieval_anchor or input_content,
                "timestamp": now.isoformat(),
            },
            module_instances=[],
            event_log=[],
            final_output="",
            created_at=now,
            updated_at=now,
            narrative_id=narrative_id,
            agent_id=agent_id,
            user_id=user_id,
        )

        logger.debug(f"Created Event: {event_id}")

        if save_to_db:
            await self.save(event)

        return event

    async def save(self, event: Event) -> int:
        """
        Save an Event to the database

        Args:
            event: Event object

        Returns:
            Number of affected rows
        """
        event_data = {
            "event_id": event.id,
            "trigger": event.trigger.value,
            "trigger_source": event.trigger_source,
            "narrative_id": event.narrative_id,
            "agent_id": event.agent_id,
            "user_id": event.user_id,
            "env_context": json.dumps(event.env_context),
            "module_instances": json.dumps([m.model_dump(mode='json') for m in event.module_instances]),
            "event_log": json.dumps([log.model_dump(mode='json') for log in event.event_log]),
            "final_output": event.final_output,
            "event_embedding": json.dumps(event.event_embedding) if event.event_embedding else None,
            "embedding_text": event.embedding_text,
        }

        db = await self._get_db_client()
        return await db.insert("events", event_data)

    async def update(
        self,
        event_id: str,
        update_data: Dict[str, Any]
    ) -> int:
        """
        Update an Event

        Args:
            event_id: Event ID
            update_data: Update data

        Returns:
            Number of affected rows
        """
        db = await self._get_db_client()
        return await db.update(
            "events",
            filters={"event_id": event_id},
            data=update_data
        )

    async def load_by_id(self, event_id: str) -> Optional[Event]:
        """
        Load an Event from the database

        Priority: DataLoader > Repository > DatabaseClient

        Args:
            event_id: Event ID

        Returns:
            Event object, or None if not found
        """
        # Prefer DataLoader
        if self._loader is not None:
            return await self._loader.load(event_id)

        # Next, use Repository
        if self._repository is not None:
            return await self._repository.get_by_id(event_id)

        # Default: Use DatabaseClient directly
        db = await self._get_db_client()
        event_data = await db.get_one("events", {"event_id": event_id})

        if not event_data:
            return None

        return self._parse_event_data(event_data)

    async def load_by_ids(self, event_ids: List[str]) -> List[Optional[Event]]:
        """
        Batch load Events (solves N+1 problem)

        Args:
            event_ids: List of Event IDs

        Returns:
            List of Events, missing positions are None
        """
        if not event_ids:
            return []

        # Prefer DataLoader
        if self._loader is not None:
            return await self._loader.load_many(event_ids)

        # Next, use Repository
        if self._repository is not None:
            return await self._repository.get_by_ids(event_ids)

        # Default: Use DatabaseClient directly
        db = await self._get_db_client()
        event_data_list = await db.get_by_ids("events", "event_id", event_ids)

        events = []
        for event_data in event_data_list:
            if event_data is None:
                events.append(None)
            else:
                try:
                    events.append(self._parse_event_data(event_data))
                except Exception as e:
                    logger.warning(f"Failed to parse Event: {e}")
                    events.append(None)

        return events

    async def update_narrative_id(self, event_id: str, narrative_id: str) -> int:
        """Update the narrative_id of an Event"""
        return await self.update(event_id, {"narrative_id": narrative_id})

    async def duplicate(self, original_event: Event, narrative_id: str) -> Event:
        """
        Duplicate an Event (for associating with a different Narrative)

        Args:
            original_event: Original Event
            narrative_id: New Narrative ID

        Returns:
            Newly created Event
        """
        new_event_id = f"evt_{uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)

        new_event = Event(
            id=new_event_id,
            trigger=original_event.trigger,
            trigger_source=original_event.trigger_source,
            env_context=original_event.env_context.copy(),
            module_instances=original_event.module_instances.copy(),
            event_log=original_event.event_log.copy(),
            final_output=original_event.final_output,
            narrative_id=narrative_id,
            agent_id=original_event.agent_id,
            user_id=original_event.user_id,
            created_at=now,
            updated_at=now,
        )

        await self.save(new_event)
        logger.debug(f"Duplicated Event: {new_event_id}")
        return new_event

    def _parse_event_data(self, event_data: Dict[str, Any]) -> Event:
        """Parse a database row into an Event object"""
        from ..models import ModuleInstance

        # Parse JSON fields
        env_context = json.loads(event_data["env_context"]) if event_data.get("env_context") else {}
        module_instances_data = json.loads(event_data["module_instances"]) if event_data.get("module_instances") else []
        event_log_data = json.loads(event_data["event_log"]) if event_data.get("event_log") else []

        # Parse embedding
        event_embedding = None
        if event_data.get("event_embedding"):
            try:
                event_embedding = json.loads(event_data["event_embedding"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Rebuild objects - fix potentially missing fields
        module_instances = []
        event_agent_id = event_data.get("agent_id")  # Get agent_id from Event as default value
        event_id = event_data.get("event_id", "")

        for m in module_instances_data:
            try:
                # Fix missing required fields
                if "agent_id" not in m and event_agent_id:
                    m["agent_id"] = event_agent_id
                    logger.debug(f"Filled missing agent_id for ModuleInstance: {event_agent_id}")

                if "module_class" not in m:
                    logger.warning(
                        f"ModuleInstance missing required module_class field, skipping: {m}\n"
                        f"  Event ID: {event_id}"
                    )
                    continue

                if "instance_id" not in m:
                    # Use deterministic hash to generate instance_id (based on Event ID and module_class)
                    # This ensures the same instance_id is generated each time the same Event is loaded
                    module_class = m.get("module_class", "Unknown")
                    module_prefix = module_class.lower().replace("module", "").strip()
                    if not module_prefix:
                        module_prefix = "inst"

                    # Generate deterministic hash: hash(event_id + module_class)
                    hash_input = f"{event_id}_{module_class}".encode('utf-8')
                    hash_value = hashlib.md5(hash_input).hexdigest()[:8]
                    m["instance_id"] = f"{module_prefix}_legacy_{hash_value}"
                    # Legacy data compatibility: silently handle missing instance_id
                    logger.debug(
                        f"ModuleInstance missing instance_id, using deterministic placeholder: {m['instance_id']} "
                        f"(Event: {event_id}, Module: {module_class})"
                    )

                # Create ModuleInstance object
                module_instances.append(ModuleInstance(**m))
            except Exception as e:
                logger.warning(
                    f"Failed to parse ModuleInstance, skipping: {e}\n"
                    f"  Data: {m}\n"
                    f"  Event ID: {event_id}"
                )
                continue

        event_log = [EventLogEntry(**log) for log in event_log_data]

        return Event(
            id=event_data["event_id"],
            trigger=TriggerType(event_data["trigger"]),
            trigger_source=event_data["trigger_source"],
            env_context=env_context,
            module_instances=module_instances,
            event_log=event_log,
            final_output=event_data.get("final_output", ""),
            created_at=event_data["created_at"],
            updated_at=event_data["updated_at"],
            narrative_id=event_data.get("narrative_id"),
            agent_id=event_data["agent_id"],
            user_id=event_data.get("user_id"),
            event_embedding=event_embedding,
            embedding_text=event_data.get("embedding_text"),
        )
