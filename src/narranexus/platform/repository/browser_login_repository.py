"""
@file_name: browser_login_repository.py
@author:
@date: 2026-09-23
@description: Cross-process login notices, separate from permission grants.
"""
from __future__ import annotations

import uuid
from typing import Any

from narranexus.platform.utils.timezone import utc_now

from .base import BaseRepository


class BrowserLoginRepository(BaseRepository[dict[str, Any]]):
    """Store handoff state without credentials, page text or permission changes."""

    table_name = "instance_browser_login_requests"
    id_field = "request_id"

    def _row_to_entity(self, row: dict[str, Any]) -> dict[str, Any]:
        return row

    def _entity_to_row(self, entity: dict[str, Any]) -> dict[str, Any]:
        return entity

    async def request(
        self, *, agent_id: str, session_key: str, reason: str, turn_id: str, thread_id: str,
    ) -> dict:
        row = {
            "request_id": f"login_{uuid.uuid4().hex}", "agent_id": agent_id,
            "session_key": session_key, "reason": reason, "turn_id": turn_id,
            "thread_id": thread_id, "state": "pending", "connection_id": "",
            "requested_at": utc_now().isoformat(),
        }
        await self._db.insert(self.table_name, row)
        return row

    async def pending(self, agent_id: str) -> list[dict]:
        rows = await self._db.get(self.table_name, {"agent_id": agent_id}, order_by="requested_at")
        return [{
            "kind": "login", "id": row["request_id"], "agent_id": agent_id,
            "session_id": agent_id, "reason": row["reason"], "state": row["state"],
            "requested_at": row["requested_at"],
        } for row in rows if row["state"] in ("pending", "in_control")]

    async def control_changed(self, agent_id: str, session_key: str, connection_id: str, event: str) -> None:
        filters = {"agent_id": agent_id, "session_key": session_key}
        if event == "take":
            await self._db.update(self.table_name, {**filters, "state": "pending"},
                                  {"state": "in_control", "connection_id": connection_id})
        elif event in ("release", "disconnect"):
            await self._db.update(
                self.table_name, {**filters, "state": "in_control", "connection_id": connection_id},
                {"state": "completed" if event == "release" else "pending", "connection_id": ""},
            )
        else:
            raise ValueError("Unknown login control event")

    async def retire(self, request_id: str) -> None:
        await self._db.delete(self.table_name, {"request_id": request_id})

    async def clear_for_agent(self, agent_id: str) -> None:
        await self._db.delete(self.table_name, {"agent_id": agent_id})
