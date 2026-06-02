"""
@file_name: narrative_repository.py
@author: NetMind.AI
@date: 2025-12-22
@description: Narrative Repository

Responsibilities:
- Persistence of Narrative entities
- Serialization/deserialization of JSON fields
- Query by agent_id, user_id, and other conditions

Design notes:
- Inherits from BaseRepository, reusing common CRUD methods
- Unified JSON field parsing logic, eliminating duplicate code
- Supports batch loading, solving the N+1 problem
"""

import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.narrative.models import (
    Narrative,
    NarrativeType,
    NarrativeInfo,
    DynamicSummaryEntry,
)
from xyz_agent_context.schema.module_schema import ModuleInstance


class NarrativeRepository(BaseRepository[Narrative]):
    """
    Narrative Repository implementation

    Usage example:
        repo = NarrativeRepository(db_client)

        # Get a single Narrative
        narrative = await repo.get_by_id("nar_123")

        # Batch fetch (solving the N+1 problem)
        narratives = await repo.get_by_ids(["nar_1", "nar_2", "nar_3"])

        # Query by Agent and User
        narratives = await repo.get_by_agent_user("agent_1", "user_1")

        # Save a Narrative
        await repo.save(narrative)
    """

    table_name = "narratives"
    id_field = "narrative_id"

    # Page size + hard ceiling for the paged user-filter scan (see
    # _scan_agent_filter_user). The page balances round-trips vs overfetch;
    # the ceiling bounds worst-case work for an agent with a huge number of
    # narratives where the target user has very few (or zero) matches.
    _USER_FILTER_PAGE = 200
    _USER_FILTER_MAX_SCAN = 5000

    async def _scan_agent_filter_user(
        self,
        agent_id: str,
        user_id: str,
        limit: int,
        require_embedding: bool = False,
    ) -> List[Narrative]:
        """Page through an agent's narratives (newest first) and collect up
        to `limit` whose actors include `user_id`.

        Replaces the old "fetch limit*2, filter in memory" approach, which
        silently dropped recall once an agent accumulated more than ~limit*2
        narratives across all its users. Scans in pages until enough matches
        are found, the rows are exhausted, or `_USER_FILTER_MAX_SCAN` rows
        have been examined. No JSON SQL — portable across MySQL and SQLite.
        """
        narratives: List[Narrative] = []
        scanned = 0
        offset = 0
        while len(narratives) < limit and scanned < self._USER_FILTER_MAX_SCAN:
            rows = await self._db.get(
                self.table_name,
                filters={"agent_id": agent_id},
                limit=self._USER_FILTER_PAGE,
                offset=offset,
                order_by="updated_at DESC",
            )
            if not rows:
                break  # rows exhausted
            offset += len(rows)
            scanned += len(rows)
            for row in rows:
                if require_embedding and not row.get("routing_embedding"):
                    continue
                try:
                    narrative = self._row_to_entity(row)
                    if any(actor.id == user_id for actor in narrative.narrative_info.actors):
                        narratives.append(narrative)
                        if len(narratives) >= limit:
                            break
                except Exception as e:
                    logger.warning(f"Failed to parse Narrative: {e}")
                    continue
            if len(rows) < self._USER_FILTER_PAGE:
                break  # last page
        return narratives

    async def get_by_agent_user(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10
    ) -> List[Narrative]:
        """
        Get all Narratives associated with a specific Agent and User

        Note: user_id lives inside the narrative_info JSON actors array, so
        the user filter is applied in Python. To avoid a recall cliff under
        high tenancy (one agent serving many users — a fixed `limit*2`
        prefetch could miss the target user's narratives entirely), we
        page through the agent's narratives by updated_at and keep scanning
        until we have `limit` matches or the rows are exhausted. This is
        dialect-agnostic (no JSON SQL needed, works on both MySQL and
        SQLite) and bounded by `_USER_FILTER_MAX_SCAN`.

        Args:
            agent_id: Agent ID
            user_id: User ID
            limit: Maximum number of results

        Returns:
            List of Narratives (sorted by updated_at descending)
        """
        logger.debug(f"    → NarrativeRepository.get_by_agent_user({agent_id}, {user_id})")

        narratives = await self._scan_agent_filter_user(
            agent_id=agent_id,
            user_id=user_id,
            limit=limit,
        )

        logger.debug(f"    ← NarrativeRepository.get_by_agent_user: {len(narratives)} found")
        return narratives

    async def get_by_agent(
        self,
        agent_id: str,
        limit: int = 50
    ) -> List[Narrative]:
        """
        Get all Narratives for an Agent

        Args:
            agent_id: Agent ID
            limit: Maximum number of results

        Returns:
            List of Narratives (sorted by updated_at descending)
        """
        logger.debug(f"    → NarrativeRepository.get_by_agent({agent_id})")
        return await self.find(
            filters={"agent_id": agent_id},
            limit=limit,
            order_by="updated_at DESC"
        )

    async def count_default_narratives(
        self,
        agent_id: str,
        user_id: Optional[str] = None
    ) -> int:
        """
        Count the number of default Narratives for an agent-user combination

        Used to check whether default Narratives have been initialized.

        Args:
            agent_id: Agent ID
            user_id: User ID (optional)

        Returns:
            Number of default Narratives
        """
        # Build ID matching pattern
        if user_id:
            pattern = f"{agent_id}_{user_id}_default_%"
        else:
            pattern = f"{agent_id}_default_%"

        query = """
            SELECT COUNT(*) as count
            FROM narratives
            WHERE agent_id = %s
              AND is_special = 'default'
              AND narrative_id LIKE %s
        """

        result = await self._db.execute(query, params=(agent_id, pattern), fetch=True)

        if result and result[0]:
            return result[0].get('count', 0)
        return 0

    async def get_default_narratives(
        self,
        agent_id: str,
        user_id: Optional[str] = None
    ) -> List[Narrative]:
        """
        Get all default Narratives for an agent-user combination

        Args:
            agent_id: Agent ID
            user_id: User ID (optional)

        Returns:
            List of default Narratives (sorted by narrative_id)
        """
        # Build ID matching pattern
        if user_id:
            pattern = f"{agent_id}_{user_id}_default_%"
        else:
            pattern = f"{agent_id}_default_%"

        query = """
            SELECT *
            FROM narratives
            WHERE agent_id = %s
              AND is_special = 'default'
              AND narrative_id LIKE %s
            ORDER BY narrative_id
        """

        rows = await self._db.execute(query, params=(agent_id, pattern), fetch=True)

        narratives = []
        for row in rows:
            try:
                narrative = self._row_to_entity(row)
                narratives.append(narrative)
            except Exception as e:
                logger.warning(f"Failed to parse default Narrative: {e}")
                continue

        return narratives

    async def get_narratives_by_participant(
        self,
        user_id: str,
        agent_id: str,
        limit: int = 50
    ) -> List[Narrative]:
        """
        Get all Narratives where the user participates as PARTICIPANT (2026-01-21 P0-4)

        Uses MySQL JSON_CONTAINS to query narrative_info.actors for records
        containing {id: user_id, type: "participant"}.

        Use cases:
        - Sales scenario: Target user is marked as PARTICIPANT
        - User is not the Narrative creator, but is associated via a Job

        Args:
            user_id: User ID
            agent_id: Agent ID
            limit: Maximum number of results

        Returns:
            List of Narratives where the user is a PARTICIPANT
        """
        logger.debug(f"    → NarrativeRepository.get_narratives_by_participant({user_id}, {agent_id})")

        # Use JSON_CONTAINS to query the actors array
        # Find records where actors contain {id: user_id, type: "participant"}
        query = """
            SELECT *
            FROM narratives
            WHERE agent_id = %s
              AND JSON_CONTAINS(
                  JSON_EXTRACT(narrative_info, '$.actors'),
                  JSON_OBJECT('id', %s, 'type', 'participant')
              )
            ORDER BY updated_at DESC
            LIMIT %s
        """

        rows = await self._db.execute(query, params=(agent_id, user_id, limit), fetch=True)

        narratives = []
        for row in rows:
            try:
                narrative = self._row_to_entity(row)
                narratives.append(narrative)
            except Exception as e:
                logger.warning(f"Failed to parse PARTICIPANT Narrative: {e}")
                continue

        logger.debug(f"    ← NarrativeRepository.get_narratives_by_participant: {len(narratives)} found")
        return narratives

    async def get_with_embedding(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Narrative]:
        """
        Get Narratives that have routing_embedding (for vector retrieval)

        Args:
            agent_id: Agent ID
            user_id: User ID (optional, for filtering)
            limit: Maximum number of results

        Returns:
            List of Narratives with embeddings
        """
        logger.debug(f"    → NarrativeRepository.get_with_embedding({agent_id})")

        if user_id:
            # Paged scan + user filter (avoids the limit*2 recall cliff under
            # high tenancy); only keep rows that carry a routing_embedding.
            narratives = await self._scan_agent_filter_user(
                agent_id=agent_id,
                user_id=user_id,
                limit=limit,
                require_embedding=True,
            )
        else:
            # No user filter: a single bounded fetch is sufficient. Overfetch
            # a bit to absorb rows without an embedding, then cap at limit.
            rows = await self._db.get(
                self.table_name,
                filters={"agent_id": agent_id},
                limit=limit * 2,
                order_by="updated_at DESC",
            )
            narratives = []
            for row in rows:
                if not row.get("routing_embedding"):
                    continue
                try:
                    narratives.append(self._row_to_entity(row))
                    if len(narratives) >= limit:
                        break
                except Exception as e:
                    logger.warning(f"Failed to parse Narrative: {e}")
                    continue

        logger.debug(f"    ← NarrativeRepository.get_with_embedding: {len(narratives)} found")
        return narratives

    def _row_to_entity(self, row: Dict[str, Any]) -> Narrative:
        """
        Convert a database row to a Narrative object

        Handles JSON field deserialization:
        - narrative_info: JSON -> NarrativeInfo
        - active_instances: JSON -> List[ModuleInstance]
        - event_ids: JSON -> List[str]
        - dynamic_summary: JSON -> List[DynamicSummaryEntry]
        - env_variables: JSON -> Dict
        - topic_keywords: JSON -> List[str]
        - routing_embedding: JSON -> List[float]
        """
        # Parse JSON fields
        narrative_info_data = self._parse_json_field(row.get("narrative_info"), {})
        active_instances_data = self._parse_json_field(row.get("active_instances"), [])
        instance_history_ids = self._parse_json_field(row.get("instance_history_ids"), [])
        event_ids = self._parse_json_field(row.get("event_ids"), [])
        dynamic_summary_data = self._parse_json_field(row.get("dynamic_summary"), [])
        env_variables = self._parse_json_field(row.get("env_variables"), {})
        related_narrative_ids = self._parse_json_field(row.get("related_narrative_ids"), [])

        # Parse routing index fields
        topic_keywords = self._parse_json_field(row.get("topic_keywords"), [])
        topic_hint = row.get("topic_hint", "") or ""
        routing_embedding = self._parse_json_field(row.get("routing_embedding"), None)

        # Parse timestamps
        embedding_updated_at = self._parse_datetime_field(row.get("embedding_updated_at"))
        events_since_last_embedding_update = row.get("events_since_last_embedding_update", 0) or 0

        # Reconstruct nested objects
        narrative_info = NarrativeInfo(**narrative_info_data)
        dynamic_summary = [DynamicSummaryEntry(**s) for s in dynamic_summary_data]
        active_instances = [ModuleInstance(**inst) for inst in active_instances_data]

        # main_chat_instance_id is deprecated, set to Optional (2026-01-21 P1-1)
        main_chat_instance_id = row.get("main_chat_instance_id")  # May be None

        return Narrative(
            id=row["narrative_id"],
            type=NarrativeType(row["type"]),
            agent_id=row["agent_id"],
            narrative_info=narrative_info,
            main_chat_instance_id=main_chat_instance_id,
            active_instances=active_instances,
            instance_history_ids=instance_history_ids,
            event_ids=event_ids,
            dynamic_summary=dynamic_summary,
            env_variables=env_variables,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            related_narrative_ids=related_narrative_ids,
            # Special flag
            is_special=row.get("is_special", "other"),
            # Routing index fields
            topic_keywords=topic_keywords,
            topic_hint=topic_hint,
            routing_embedding=routing_embedding,
            embedding_updated_at=embedding_updated_at,
            events_since_last_embedding_update=events_since_last_embedding_update,
        )

    def _entity_to_row(self, entity: Narrative) -> Dict[str, Any]:
        """
        Convert a Narrative object to a database row

        Handles JSON field serialization:
        - narrative_info: NarrativeInfo -> JSON
        - active_instances: List[ModuleInstance] -> JSON
        - event_ids: List[str] -> JSON
        - dynamic_summary: List[DynamicSummaryEntry] -> JSON
        - env_variables: Dict -> JSON
        - topic_keywords: List[str] -> JSON
        - routing_embedding: List[float] -> JSON
        """
        return {
            "narrative_id": entity.id,
            "type": entity.type.value,
            "agent_id": entity.agent_id,
            "narrative_info": json.dumps(
                entity.narrative_info.model_dump(mode='json'),
                ensure_ascii=False
            ),
            # 2026-01-21 P1-1: main_chat_instance_id has been removed from the database, no longer inserted
            "active_instances": json.dumps(
                [inst.model_dump(mode='json') for inst in entity.active_instances],
                ensure_ascii=False
            ),
            "instance_history_ids": json.dumps(entity.instance_history_ids, ensure_ascii=False),
            "event_ids": json.dumps(entity.event_ids, ensure_ascii=False),
            "dynamic_summary": json.dumps(
                [s.model_dump(mode='json') for s in entity.dynamic_summary],
                ensure_ascii=False
            ),
            "env_variables": json.dumps(entity.env_variables, ensure_ascii=False),
            "related_narrative_ids": json.dumps(entity.related_narrative_ids, ensure_ascii=False),
            # Special flag
            "is_special": entity.is_special,
            # Routing index fields
            "topic_keywords": json.dumps(entity.topic_keywords, ensure_ascii=False),
            "topic_hint": entity.topic_hint,
            "routing_embedding": json.dumps(entity.routing_embedding) if entity.routing_embedding else None,
            "embedding_updated_at": entity.embedding_updated_at.isoformat() if entity.embedding_updated_at else None,
            "events_since_last_embedding_update": entity.events_since_last_embedding_update,
        }

    @staticmethod
    def _parse_json_field(value: Any, default: Any) -> Any:
        """
        Parse a JSON field

        Args:
            value: Field value (may be a str or an already parsed object)
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

    @staticmethod
    def _parse_datetime_field(value: Any) -> Optional[datetime]:
        """
        Parse a datetime field

        Args:
            value: Field value (may be a datetime, str, or None)

        Returns:
            datetime object or None
        """
        if value is None:
            return None

        if isinstance(value, datetime):
            return value

        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        return None
