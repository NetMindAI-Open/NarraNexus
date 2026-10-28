"""
@file_name: event_repository.py
@author: NetMind.AI
@date: 2025-11-28
@description: Event Repository

Responsibilities:
- Persistence of Event entities
- Serialization/deserialization of JSON fields
- Query by narrative_id, agent_id, and other conditions
"""

import json
from typing import List, Dict, Any, Optional
from loguru import logger

from .base import BaseRepository
from narranexus.platform.narrative.models import Event, EventLogEntry, TriggerType
from narranexus.platform.schema.module_schema import ModuleInstance


class EventRepository(BaseRepository[Event]):
    """
    Event Repository implementation

    Usage example:
        repo = EventRepository(db_client)

        # Get a single Event
        event = await repo.get_by_id("evt_123")

        # Batch fetch (solving the N+1 problem)
        events = await repo.get_by_ids(["evt_1", "evt_2", "evt_3"])

        # Query by Narrative
        events = await repo.get_by_narrative("nar_456")

        # Save an Event
        await repo.save(event)
    """

    table_name = "events"
    id_field = "event_id"

    async def get_by_narrative(
        self,
        narrative_id: str,
        limit: int = 100
    ) -> List[Event]:
        """
        Get all Events under a Narrative

        Args:
            narrative_id: Narrative ID
            limit: Maximum number of results

        Returns:
            List of Events (sorted by creation time descending)
        """
        logger.debug(f"    → EventRepository.get_by_narrative({narrative_id})")
        return await self.find(
            filters={"narrative_id": narrative_id},
            limit=limit,
            order_by="created_at DESC"
        )

    async def get_by_agent_user(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 100
    ) -> List[Event]:
        """
        Get Events for a specific Agent and User

        Args:
            agent_id: Agent ID
            user_id: User ID
            limit: Maximum number of results

        Returns:
            List of Events (sorted by creation time descending)
        """
        logger.debug(f"    → EventRepository.get_by_agent_user({agent_id}, {user_id})")
        return await self.find(
            filters={"agent_id": agent_id, "user_id": user_id},
            limit=limit,
            order_by="created_at DESC"
        )

    async def get_recent_events(
        self,
        agent_id: str,
        limit: int = 10
    ) -> List[Event]:
        """
        Get recent Events for an Agent

        Args:
            agent_id: Agent ID
            limit: Maximum number of results

        Returns:
            List of Events (sorted by creation time descending)
        """
        logger.debug(f"    → EventRepository.get_recent_events({agent_id}, limit={limit})")
        return await self.find(
            filters={"agent_id": agent_id},
            limit=limit,
            order_by="created_at DESC"
        )

    async def update_narrative_id(
        self,
        event_id: str,
        narrative_id: str
    ) -> int:
        """
        Update the narrative_id of an Event

        Args:
            event_id: Event ID
            narrative_id: New Narrative ID

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → EventRepository.update_narrative_id({event_id}, {narrative_id})")
        return await self.update(event_id, {"narrative_id": narrative_id})

    async def update_final_output(
        self,
        event_id: str,
        final_output: str,
        event_log: Optional[List[EventLogEntry]] = None,
        module_instances: Optional[List[ModuleInstance]] = None
    ) -> int:
        """
        Update the execution result of an Event

        Args:
            event_id: Event ID
            final_output: Final output
            event_log: Event log (optional)
            module_instances: List of module instances (optional)

        Returns:
            Number of affected rows
        """
        logger.debug(f"    → EventRepository.update_final_output({event_id})")

        update_data = {"final_output": final_output}

        if event_log is not None:
            update_data["event_log"] = json.dumps(
                [log.model_dump(mode='json') for log in event_log],
                ensure_ascii=False
            )

        if module_instances is not None:
            update_data["module_instances"] = json.dumps(
                [m.model_dump() for m in module_instances],
                ensure_ascii=False
            )

        return await self.update(event_id, update_data)

    def _row_to_entity(self, row: Dict[str, Any]) -> Event:
        """
        Convert a database row to an Event object

        Handles JSON field deserialization:
        - env_context: JSON -> Dict
        - module_instances: JSON -> List[ModuleInstance]
        - event_log: JSON -> List[EventLogEntry]
        """
        # Parse JSON fields
        env_context = self._parse_json_field(row.get("env_context"), {})
        module_instances_data = self._parse_json_field(row.get("module_instances"), [])
        event_log_data = self._parse_json_field(row.get("event_log"), [])

        # Rebuild nested objects
        module_instances = [ModuleInstance(**m) for m in module_instances_data]
        event_log = [EventLogEntry(**log) for log in event_log_data]

        return Event(
            id=row["event_id"],
            trigger=TriggerType(row["trigger"]),
            trigger_source=row["trigger_source"],
            env_context=env_context,
            module_instances=module_instances,
            event_log=event_log,
            final_output=row.get("final_output", ""),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            narrative_id=row.get("narrative_id"),
            agent_id=row["agent_id"],
            user_id=row.get("user_id"),
        )

    def _entity_to_row(self, entity: Event) -> Dict[str, Any]:
        """
        Convert an Event object to a database row

        Handles JSON field serialization:
        - env_context: Dict -> JSON
        - module_instances: List[ModuleInstance] -> JSON
        - event_log: List[EventLogEntry] -> JSON
        """
        return {
            "event_id": entity.id,
            "trigger": entity.trigger.value,
            "trigger_source": entity.trigger_source,
            "narrative_id": entity.narrative_id,
            "agent_id": entity.agent_id,
            "user_id": entity.user_id,
            "env_context": json.dumps(entity.env_context, ensure_ascii=False),
            "module_instances": json.dumps(
                [m.model_dump() for m in entity.module_instances],
                ensure_ascii=False
            ),
            "event_log": json.dumps(
                [log.model_dump(mode='json') for log in entity.event_log],
                ensure_ascii=False
            ),
            "final_output": entity.final_output,
        }

    @staticmethod
    def _parse_json_field(value: Any, default: Any) -> Any:
        """
        Parse a JSON field

        Args:
            value: Field value (may be str or an already parsed object)
            default: Default value

        Returns:
            Parsed value
        """
        if value is None:
            return default

        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default

        return value

    # ------------------------------------------------------------------
    # Diagnostics projections (manyfold pull channel)
    # ------------------------------------------------------------------

    # Lifecycle-only columns for run summaries. The projection is load-
    # bearing, not cosmetic: the un-projected row set of a long-lived
    # agent carries every MEDIUMTEXT event_log it ever produced (multi-
    # hour runs are first-class, rule #14 — those logs reach tens of MB
    # each), and a diagnostic read must never OOM the process it is
    # diagnosing.
    DIAG_SUMMARY_FIELDS = [
        "event_id",
        "trigger",
        "trigger_source",
        "state",
        "started_at",
        "finished_at",
        "tool_call_count",
        "current_stage",
        "error_message",
        "narrative_id",
    ]

    async def diagnostic_summaries(
        self, agent_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Raw projected rows, newest first — dicts on purpose (the
        Event entity would deserialize the heavy JSON columns this
        projection exists to exclude)."""
        return await self._db.get(
            self.table_name,
            {"agent_id": agent_id},
            limit=limit,
            order_by="started_at DESC",
            fields=self.DIAG_SUMMARY_FIELDS,
        )

    async def diagnostic_full(
        self, agent_id: str, event_id: str
    ) -> Optional[Dict[str, Any]]:
        """One full raw row, scoped WHERE agent_id AND event_id — a
        foreign agent's event reads as missing (no existence oracle)."""
        return await self._db.get_one(
            self.table_name, {"agent_id": agent_id, "event_id": event_id}
        )
