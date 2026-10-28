"""
@file_name: agent_repository.py
@author: NetMind.AI
@date: 2025-12-02
@description: Agent Repository - Data access layer for Agent data

Responsibilities:
- CRUD operations for Agents
- Query by creator or type
"""

import json
from typing import Dict, Any, Optional
from loguru import logger

from .base import BaseRepository
from narranexus.platform.schema import Agent, normalize_agent_row_text


class AgentRepository(BaseRepository[Agent]):
    """
    Agent Repository implementation

    Usage example:
        repo = AgentRepository(db_client)

        # Get an Agent
        agent = await repo.get_agent("agent_123")

        # Add an Agent
        await repo.add_agent(agent_id, agent_name, created_by)

        # Update an Agent
        await repo.update_agent(agent_id, {"agent_name": "new_name"})
    """

    table_name = "agents"
    id_field = "id"

    _json_fields = {"agent_metadata"}

    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an Agent"""
        logger.debug(f"    → AgentRepository.get_agent({agent_id})")
        return await self.find_one({"agent_id": agent_id})

    async def resolve_owner(self, agent_id: str) -> Optional[str]:
        """The agent's owner (``agents.created_by``); ``""`` when the agent
        is unknown, ``None`` when the LOOKUP ITSELF failed.

        The ONE answer to "who owns this agent". Visibility checks (the
        run observe endpoint), channel triggers and the OpenAI-compat
        route all resolve through here — the pattern used to live as
        three private copies, which is exactly how ownership semantics
        drift apart. NOTE this is deliberately NOT ``events.user_id``:
        that column stores the run's TRIGGERING key (a team run stores
        the sender, ``usr_<uid>`` or a relaying agent_id), not ownership.

        The ""/None split exists because callers make SECURITY decisions on
        this value (PR #258 review #4): "agent does not exist" and "the
        database hiccuped" must not collapse into one answer, or a db outage
        presents as a batch of users' agents vanishing with only a warning
        log to show for it. Callers that only gate on truthiness are
        unaffected (both are falsy); authorization callers map None to a
        5xx, never to "not found".
        """
        if not agent_id:
            return ""
        try:
            row = await self._db.get_one(self.table_name, {"agent_id": agent_id})
            return (row or {}).get("created_by", "") or ""
        except Exception as e:  # noqa: BLE001 — surfaced as None, callers decide
            logger.warning(f"AgentRepository.resolve_owner({agent_id}) failed: {e}")
            return None

    async def add_agent(
        self,
        agent_id: str,
        agent_name: str,
        created_by: str,
        agent_description: Optional[str] = None,
        agent_type: Optional[str] = None,
        agent_metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Add a new Agent.

        Text fields are stored normalized (:func:`normalize_agent_row_text`).
        A row holding an unstripped name can never be cleaned up afterwards:
        the update path compares normalized values, so saving the "same" name
        without the stray space is judged a no-op and never written.

        This repository is the main enforcement point but NOT the only writer
        of the table — see the invariant note on
        :func:`~narranexus.platform.schema.entity_schema.agent_field_matches`
        for the paths that raw-insert and normalize at their own edge.
        """
        logger.debug(f"    → AgentRepository.add_agent({agent_id})")

        fields = normalize_agent_row_text({
            "agent_name": agent_name,
            "agent_description": agent_description,
        })
        agent = Agent(
            agent_id=agent_id,
            agent_name=fields["agent_name"],
            created_by=created_by,
            agent_description=fields["agent_description"],
            agent_type=agent_type,
            agent_metadata=agent_metadata,
        )

        return await self.insert(agent)

    async def update_agent(self, agent_id: str, updates: Dict[str, Any]) -> int:
        """Update Agent information.

        Text fields are normalized on the way in, same as :meth:`add_agent`,
        so "the stored form" is a property of the table rather than of whoever
        happened to write it. Callers that compare before writing (the update
        route, the awareness tool) normalize too — that is what lets them trust
        "already equal" and then verify by re-reading.
        """
        logger.debug(f"    → AgentRepository.update_agent({agent_id})")

        updates = normalize_agent_row_text(updates)

        # Serialize JSON fields
        if "agent_metadata" in updates and not isinstance(updates["agent_metadata"], str):
            updates["agent_metadata"] = json.dumps(updates["agent_metadata"], ensure_ascii=False)

        query = f"""
            UPDATE {self.table_name}
            SET {', '.join(f'`{k}` = %s' for k in updates.keys())}
            WHERE agent_id = %s
        """

        params = list(updates.values()) + [agent_id]
        result = await self._db.execute(query, params=tuple(params), fetch=False)
        return result if isinstance(result, int) else 0

    def _row_to_entity(self, row: Dict[str, Any]) -> Agent:
        """Convert a database row to an Agent object"""
        metadata = self._parse_json_field(row.get("agent_metadata"), None)

        return Agent(
            id=row.get("id"),
            agent_id=row["agent_id"],
            agent_name=row["agent_name"],
            created_by=row["created_by"],
            agent_description=row.get("agent_description"),
            agent_type=row.get("agent_type"),
            is_public=bool(row.get("is_public", 0)),
            agent_metadata=metadata,
            agent_create_time=row.get("agent_create_time"),
            agent_update_time=row.get("agent_update_time"),
        )

    def _entity_to_row(self, entity: Agent) -> Dict[str, Any]:
        """Convert an Agent object to a database row"""
        return {
            "agent_id": entity.agent_id,
            "agent_name": entity.agent_name,
            "created_by": entity.created_by,
            "agent_description": entity.agent_description,
            "agent_type": entity.agent_type,
            "is_public": int(entity.is_public),
            "agent_metadata": json.dumps(entity.agent_metadata, ensure_ascii=False) if entity.agent_metadata else None,
        }

    @staticmethod
    def _parse_json_field(value: Any, default: Any) -> Any:
        """Parse a JSON field"""
        if value is None:
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return value
