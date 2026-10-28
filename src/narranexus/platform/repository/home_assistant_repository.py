"""
@file_name: home_assistant_repository.py
@author: NetMind.AI
@date: 2026-07-14
@description: Home Assistant binding repository — pure CRUD over
`instance_homeassistant_bindings`.

One row per agent: the connection config (base_url + token + verify_tls) is
stored as a JSON string in `config_json`. Keying on `agent_id` lets each agent
bind its own Home Assistant — the intended model when a user runs multiple HA
instances (e.g. home vs. office) and wants different agents to control each.
Parsing into `HAConfig` is the module layer's job (repository stays raw-CRUD).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger

from .base import BaseRepository


@dataclass
class HABindingRow:
    """A raw `instance_homeassistant_bindings` row."""

    agent_id: str
    config_json: str
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class HomeAssistantBindingRepository(BaseRepository[HABindingRow]):
    """CRUD for the per-agent Home Assistant binding."""

    table_name = "instance_homeassistant_bindings"
    id_field = "agent_id"

    async def get_by_agent(self, agent_id: str) -> Optional[HABindingRow]:
        """Return the binding row for an agent, or None."""
        return await self.find_one({"agent_id": agent_id})

    async def upsert_config(self, agent_id: str, config_json: str) -> bool:
        """Insert or update the binding's config_json for an agent.

        Named `upsert_config` (not `upsert`) so it doesn't clash with
        BaseRepository.upsert(entity) — different signature/semantics.
        """
        try:
            existing = await self.get_by_agent(agent_id)
            if existing:
                await self.update(agent_id, {"config_json": config_json})
            else:
                await self.insert(HABindingRow(agent_id=agent_id, config_json=config_json))
            return True
        except Exception as e:  # noqa: BLE001 — surface as False, never crash the caller
            logger.exception(f"Failed to upsert HA binding for {agent_id}: {e}")
            return False

    async def delete_by_agent(self, agent_id: str) -> int:
        """Remove the binding for an agent. Returns affected rows."""
        return await self.delete(agent_id)

    def _row_to_entity(self, row: Dict[str, Any]) -> HABindingRow:
        return HABindingRow(
            id=row.get("id"),
            agent_id=row["agent_id"],
            config_json=row.get("config_json", "") or "",
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: HABindingRow) -> Dict[str, Any]:
        return {
            "agent_id": entity.agent_id,
            "config_json": entity.config_json,
        }
