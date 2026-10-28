"""
@file_name: browser_policy_repository.py
@author:
@date: 2026-09-22
@description: Data access for per-agent browser permissions.

CRUD over ``instance_browser_policies`` (one row per agent). The policy object
owns all the decision logic; this layer only moves JSON in and out.

Persists the owner's per-origin arbitrary-script permission, not website
access: ordinary HTTP(S) browsing needs no permission. The document is shared
by the MCP host (which enforces it) and the API process (which edits it).
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from loguru import logger

from narranexus.platform.utils.timezone import utc_now

from .base import BaseRepository


class BrowserPolicyRepository(BaseRepository[Dict[str, Any]]):
    """Read/write one agent's browser permission document."""

    table_name = "instance_browser_policies"
    id_field = "agent_id"

    def _row_to_entity(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return row

    def _entity_to_row(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        return entity

    async def get_policy(self, agent_id: str) -> Optional[dict]:
        """The agent's stored policy document, or None if it has none yet.

        A row whose JSON will not parse is treated as absent rather than
        raising, so arbitrary scripts fall back to denied; ordinary HTTP(S)
        browsing remains unrestricted.
        """
        row = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        if not row:
            return None
        raw = row.get("policy_json")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "browser policy for {} is not valid JSON; falling back to defaults", agent_id
            )
            return None

    async def save_policy(self, agent_id: str, policy: dict) -> None:
        """Insert-or-update the agent's policy document."""
        payload = json.dumps(policy, ensure_ascii=False)
        now = utc_now().isoformat()
        existing = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        if existing:
            await self._db.update(
                self.table_name,
                {"agent_id": agent_id},
                {"policy_json": payload, "updated_at": now},
            )
        else:
            await self._db.insert(
                self.table_name,
                {
                    "agent_id": agent_id,
                    "policy_json": payload,
                    "created_at": now,
                    "updated_at": now,
                },
            )
