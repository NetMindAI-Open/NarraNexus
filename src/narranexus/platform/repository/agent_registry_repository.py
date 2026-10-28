"""
@file_name: agent_registry_repository.py
@author: NarraNexus
@date: 2026-08-04
@description: Data access for ``bus_agent_registry`` — the peer-discovery
directory an agent is found in by other agents.

Why it lives here and not in the message_bus module
---------------------------------------------------
The table used to be written from exactly one place: an inline block inside
``MessageBusModule.gather``. That made discovery a side effect of
taking a turn — an agent created and configured but not yet run was absent from
the directory, and the only writer hardcoded ``capabilities=[]`` (P1 section 02).

Fixing that means the write has to happen from creation and configuration paths
too: an HTTP route, an Awareness MCP tool, the skill installer. Those must not
reach into a module's internals (iron rule #3 — modules are independent and
hot-pluggable), and per the project layout repositories never live inside a
module. So table access moves here, and
``message_bus/agent_discovery_sync.py`` owns the "what should the row say"
policy above it — in the bus package, because that is the domain this table
belongs to; ``services/`` hosts background workers, not synchronous policy
called from routes and tools.

``LocalMessageBus`` keeps its own reader/writer for the bus-facing API surface
(``register_agent`` / ``get_agent_profile`` / ``search_agents``); this
repository is the platform-side seam for keeping the row true.
"""

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .base import BaseRepository

if TYPE_CHECKING:  # pragma: no cover - typing only
    from narranexus.platform.message_bus.schemas import BusAgentInfo

# The row entity is the bus's own ``BusAgentInfo`` (one model for one table
# beats two that drift), but it is imported lazily inside the methods:
# ``narranexus.platform.message_bus.__init__`` pulls in LocalMessageBus, the
# trigger and the channel registry, and an HTTP route that merely creates an
# agent has no business loading those. Same lazy-import convention the bus
# itself uses when it needs a repository.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentRegistryRepository(BaseRepository["BusAgentInfo"]):
    """CRUD for the peer-discovery directory row of one agent."""

    table_name = "bus_agent_registry"
    id_field = "agent_id"

    def _row_to_entity(self, row: Dict[str, Any]) -> "BusAgentInfo":
        from narranexus.platform.message_bus.schemas import BusAgentInfo

        raw = row.get("capabilities")
        if isinstance(raw, str):
            try:
                caps = json.loads(raw) if raw.strip() else []
            except json.JSONDecodeError:
                # A hand-edited row must not break discovery for everyone.
                caps = []
        else:
            caps = list(raw or [])
        return BusAgentInfo(
            agent_id=row["agent_id"],
            owner_user_id=row.get("owner_user_id", ""),
            capabilities=caps,
            description=row.get("description") or "",
            visibility=row.get("visibility", "private"),
            registered_at=row.get("registered_at"),
            last_seen_at=row.get("last_seen_at"),
        )

    def _entity_to_row(self, entity: "BusAgentInfo") -> Dict[str, Any]:
        return {
            "agent_id": entity.agent_id,
            "owner_user_id": entity.owner_user_id,
            "capabilities": json.dumps(entity.capabilities),
            "description": entity.description,
            "visibility": entity.visibility,
            "registered_at": entity.registered_at or _now(),
            "last_seen_at": entity.last_seen_at or _now(),
        }

    async def get_profile(self, agent_id: str) -> Optional["BusAgentInfo"]:
        """The agent's directory row, or None if it has never been registered."""
        row = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        return self._row_to_entity(row) if row else None

    async def upsert_profile(
        self,
        agent_id: str,
        *,
        owner_user_id: str,
        capabilities: List[str],
        description: str,
        visibility: str,
    ) -> None:
        """Write the row so it states today's truth.

        Called on every mutation AND on every turn, so it converges rather than
        accumulating. ``registered_at`` is preserved on an existing row —
        "first seen" is a different fact from "last synced".
        """
        now = _now()
        existing = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        await self._db.upsert(
            self.table_name,
            {
                "agent_id": agent_id,
                "owner_user_id": owner_user_id,
                "capabilities": json.dumps(capabilities),
                "description": description,
                "visibility": visibility,
                "registered_at": (
                    (existing or {}).get("registered_at") or now
                ),
                "last_seen_at": now,
            },
            "agent_id",
        )
