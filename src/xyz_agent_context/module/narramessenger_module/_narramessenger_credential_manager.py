"""
@file_name: _narramessenger_credential_manager.py
@date: 2026-06-17
@description: CRUD for the ``channel_narramessenger_credentials`` table.

One row per agent. Stores the NarraMessenger runtime bearer token (the only
secret, base64-encoded like lark/telegram) plus the backend/homeserver URLs
and the agent's Matrix/NarraMessenger identity. v1 transport is Gateway
Polling + ``/chat/send`` — pure HTTP bearer-only, so no Matrix access token
is stored.

Mirrors ``telegram_module/_telegram_credential_manager.py`` shape (dataclass +
manager). Unlike Telegram there is no ``getMe``-style validation API here, so
binding/seeding writes the row directly via ``upsert``; live verification is
done by the runtime ``/status`` endpoint when needed.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.utils.database import AsyncDatabaseClient


def _encode_token(raw: str) -> str:
    if not raw:
        return ""
    return base64.b64encode(raw.encode()).decode()


def _decode_token(encoded: str) -> str:
    if not encoded:
        return ""
    return base64.b64decode(encoded.encode()).decode()


@dataclass
class NarramessengerCredential:
    """One agent's NarraMessenger binding."""

    agent_id: str
    bearer_token: str  # decoded — caller-side use only, never log
    backend_base_url: str = ""
    matrix_homeserver_url: str = ""
    matrix_user_id: str = ""
    nexus_principal_id: str = ""
    nexus_profile_id: str = ""
    bind_room_id: str = ""
    # Owner identity — drives the is_owner_interacting trust signal
    # (owner_matrix_user_id == current sender).
    owner_matrix_user_id: str = ""
    owner_name: str = ""
    connection_mode: str = "gateway"
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_public_dict(self) -> dict[str, Any]:
        """Sanitised view safe for API responses / logs (NO bearer token)."""
        return {
            "agent_id": self.agent_id,
            "backend_base_url": self.backend_base_url,
            "matrix_homeserver_url": self.matrix_homeserver_url,
            "matrix_user_id": self.matrix_user_id,
            "nexus_principal_id": self.nexus_principal_id,
            "nexus_profile_id": self.nexus_profile_id,
            "bind_room_id": self.bind_room_id,
            "owner_matrix_user_id": self.owner_matrix_user_id,
            "owner_name": self.owner_name,
            "connection_mode": self.connection_mode,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class NarramessengerCredentialManager:
    """Manages per-agent credentials in ``channel_narramessenger_credentials``."""

    TABLE = "channel_narramessenger_credentials"

    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def upsert(self, cred: NarramessengerCredential) -> None:
        """Insert or update one agent's credential row (bearer base64-encoded)."""
        now_iso = self._now_iso()
        row = {
            "agent_id": cred.agent_id,
            "bearer_token_encoded": _encode_token(cred.bearer_token),
            "backend_base_url": cred.backend_base_url,
            "matrix_homeserver_url": cred.matrix_homeserver_url,
            "matrix_user_id": cred.matrix_user_id,
            "nexus_principal_id": cred.nexus_principal_id,
            "nexus_profile_id": cred.nexus_profile_id,
            "bind_room_id": cred.bind_room_id,
            "owner_matrix_user_id": cred.owner_matrix_user_id,
            "owner_name": cred.owner_name,
            "connection_mode": cred.connection_mode or "gateway",
            "enabled": 1 if cred.enabled else 0,
            "updated_at": now_iso,
        }
        existing = await self._db.get_one(self.TABLE, {"agent_id": cred.agent_id})
        if existing:
            await self._db.update(self.TABLE, {"agent_id": cred.agent_id}, row)
            logger.info(
                f"[narramessenger:{cred.agent_id}] credential updated "
                f"(matrix_user_id={cred.matrix_user_id})"
            )
        else:
            row["created_at"] = now_iso
            await self._db.insert(self.TABLE, row)
            logger.info(
                f"[narramessenger:{cred.agent_id}] credential inserted "
                f"(matrix_user_id={cred.matrix_user_id})"
            )

    async def get(self, agent_id: str) -> Optional[NarramessengerCredential]:
        """Fetch credential by agent_id (bearer decoded). None if missing."""
        row = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not row:
            return None
        return self._row_to_cred(row)

    async def get_public(self, agent_id: str) -> Optional[dict[str, Any]]:
        cred = await self.get(agent_id)
        return cred.to_public_dict() if cred else None

    async def list_active(self) -> list[NarramessengerCredential]:
        """All enabled rows — consumed by the trigger's credential watcher."""
        rows = await self._db.get(self.TABLE, {"enabled": 1})
        return [self._row_to_cred(r) for r in rows]

    async def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        """Flip ``enabled`` without deleting — lets the trigger break out of a
        reconnect loop against a revoked bearer (HTTP 401/409)."""
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.update(
            self.TABLE, {"agent_id": agent_id}, {"enabled": 1 if enabled else 0}
        )
        return True

    async def update_owner(
        self, agent_id: str, owner_matrix_user_id: str, owner_name: str
    ) -> bool:
        """Set/refresh the owner identity used for the trust signal."""
        affected = await self._db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {
                "owner_matrix_user_id": owner_matrix_user_id,
                "owner_name": owner_name,
                "updated_at": self._now_iso(),
            },
        )
        return bool(affected)

    async def unbind(self, agent_id: str) -> bool:
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.delete(self.TABLE, {"agent_id": agent_id})
        logger.info(f"[narramessenger:{agent_id}] credential unbound")
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_cred(self, row: dict[str, Any]) -> NarramessengerCredential:
        return NarramessengerCredential(
            agent_id=row.get("agent_id", ""),
            bearer_token=_decode_token(row.get("bearer_token_encoded", "")),
            backend_base_url=row.get("backend_base_url", "") or "",
            matrix_homeserver_url=row.get("matrix_homeserver_url", "") or "",
            matrix_user_id=row.get("matrix_user_id", "") or "",
            nexus_principal_id=row.get("nexus_principal_id", "") or "",
            nexus_profile_id=row.get("nexus_profile_id", "") or "",
            bind_room_id=row.get("bind_room_id", "") or "",
            owner_matrix_user_id=row.get("owner_matrix_user_id", "") or "",
            owner_name=row.get("owner_name", "") or "",
            connection_mode=row.get("connection_mode", "gateway") or "gateway",
            enabled=bool(row.get("enabled", 1)),
            created_at=self._parse_dt(row.get("created_at")),
            updated_at=self._parse_dt(row.get("updated_at")),
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_dt(value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
