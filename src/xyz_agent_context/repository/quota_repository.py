"""
@file_name: quota_repository.py
@author: Bin Liang
@date: 2026-04-16
@description: Data-access layer for the `user_quotas` table.

Atomic UPDATEs for deduct/grant so concurrent LLM calls from one user
do not lose counts via read-modify-write races.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .base import BaseRepository
from xyz_agent_context.schema.quota_schema import Quota, QuotaStatus


class QuotaRepository(BaseRepository[Quota]):
    table_name = "user_quotas"
    id_field = "user_id"  # logical key; surrogate PK `id` is table-internal

    async def get_by_user_id(self, user_id: str) -> Optional[Quota]:
        row = await self._db.get_one(self.table_name, {"user_id": user_id})
        return self._row_to_entity(row) if row else None

    async def create(
        self,
        user_id: str,
        initial_input_tokens: int,
        initial_output_tokens: int,
    ) -> Quota:
        now = datetime.now(timezone.utc)
        entity = Quota(
            user_id=user_id,
            initial_input_tokens=initial_input_tokens,
            initial_output_tokens=initial_output_tokens,
            created_at=now,
            updated_at=now,
        )
        await self._db.insert(self.table_name, self._entity_to_row(entity))
        fetched = await self.get_by_user_id(user_id)
        assert fetched is not None, f"insert of {user_id} failed silently"
        return fetched

    async def atomic_deduct(
        self, user_id: str, input_delta: int, output_delta: int
    ) -> None:
        """Atomic UPDATE. Flips status to 'exhausted' when either dimension's
        post-update remaining is <= 0.
        """
        sql = f"""
        UPDATE {self.table_name}
        SET used_input_tokens  = used_input_tokens  + %s,
            used_output_tokens = used_output_tokens + %s,
            status = CASE
              WHEN (initial_input_tokens + granted_input_tokens
                    - used_input_tokens - %s) <= 0
                OR (initial_output_tokens + granted_output_tokens
                    - used_output_tokens - %s) <= 0
              THEN 'exhausted'
              ELSE status
            END
        WHERE user_id = %s
        """
        await self._db.execute(
            sql,
            params=(input_delta, output_delta, input_delta, output_delta, user_id),
            fetch=False,
        )

    async def atomic_grant(
        self, user_id: str, input_delta: int, output_delta: int
    ) -> None:
        """Atomic UPDATE. Reactivates an exhausted user when the grant lifts
        remaining above zero in both dimensions.
        """
        sql = f"""
        UPDATE {self.table_name}
        SET granted_input_tokens  = granted_input_tokens  + %s,
            granted_output_tokens = granted_output_tokens + %s,
            status = CASE
              WHEN status = 'exhausted'
                AND (initial_input_tokens + granted_input_tokens + %s
                     - used_input_tokens) > 0
                AND (initial_output_tokens + granted_output_tokens + %s
                     - used_output_tokens) > 0
              THEN 'active'
              ELSE status
            END
        WHERE user_id = %s
        """
        await self._db.execute(
            sql,
            params=(input_delta, output_delta, input_delta, output_delta, user_id),
            fetch=False,
        )

    async def set_preference(
        self, user_id: str, prefer_system_override: bool
    ) -> None:
        """Atomic UPDATE of the user-choice toggle."""
        sql = f"""
        UPDATE {self.table_name}
        SET prefer_system_override = %s
        WHERE user_id = %s
        """
        await self._db.execute(
            sql,
            params=(1 if prefer_system_override else 0, user_id),
            fetch=False,
        )

    def _row_to_entity(self, row: Dict[str, Any]) -> Quota:
        return Quota(
            user_id=row["user_id"],
            initial_input_tokens=row["initial_input_tokens"],
            initial_output_tokens=row["initial_output_tokens"],
            used_input_tokens=row["used_input_tokens"],
            used_output_tokens=row["used_output_tokens"],
            granted_input_tokens=row["granted_input_tokens"],
            granted_output_tokens=row["granted_output_tokens"],
            status=QuotaStatus(row["status"]),
            prefer_system_override=bool(row.get("prefer_system_override", 0)),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _entity_to_row(self, entity: Quota) -> Dict[str, Any]:
        return {
            "user_id": entity.user_id,
            "initial_input_tokens": entity.initial_input_tokens,
            "initial_output_tokens": entity.initial_output_tokens,
            "used_input_tokens": entity.used_input_tokens,
            "used_output_tokens": entity.used_output_tokens,
            "granted_input_tokens": entity.granted_input_tokens,
            "granted_output_tokens": entity.granted_output_tokens,
            "status": entity.status.value,
            "prefer_system_override": 1 if entity.prefer_system_override else 0,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }


def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
