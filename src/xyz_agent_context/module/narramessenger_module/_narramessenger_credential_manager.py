"""
@file_name: _narramessenger_credential_manager.py
@date: 2026-07-02
@description: CRUD for the ``channel_narramessenger_credentials`` table.

One row per agent. Direct Matrix is the only transport as of Commit 7
(2026-07-02); the legacy Gateway/polling trigger was deleted in the same
commit. Fields:

  - Message-plane secret: ``matrix_access_token`` (base64-encoded in DB),
    used ONLY for Matrix HTTP API calls. Matrix rejects the Narra bearer
    with ``M_UNKNOWN_TOKEN`` — do not mix.
  - Control-plane secret: ``bearer_token`` (base64-encoded), used for
    ``/bind-agent/runtime-ready`` and the authorize-event gate. Retained
    for legacy Gateway callers (``/api/agent-gateway/*``) that don't need
    a Matrix client.
  - ``matrix_since_token``: the /sync cursor; written on every sync tick
    via the dedicated ``update_since_token()`` helper — avoids round-
    tripping the full row every few seconds.
  - ``matrix_device_id``: pins the same server-side device on reconnect
    so restarts don't spawn a fresh device on every boot.
  - ``connection_mode``: kept in the schema for existing rows but no
    longer filtered on; the ``NarramessengerTrigger`` (polling) that
    consumed ``connection_mode='gateway'`` rows was deleted in Commit 7.
    Old rows without a ``matrix_access_token`` drop out on first sync
    (MatrixTrigger.connect raises → base disables the credential →
    owner re-runs the bind flow).

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
    # ── Matrix transport fields (2026-07-02) ──────────────────────────────
    # Populated only on connection_mode == "matrix" rows. On "gateway" rows
    # they stay empty and MatrixTrigger's credential watcher skips the row.
    matrix_access_token: str = ""  # decoded syt_..., never log
    matrix_device_id: str = ""
    # Opaque /sync cursor. WRITE-HEAVY — updated on every sync response via
    # ``NarramessengerCredentialManager.update_since_token()``, NOT via a
    # full ``upsert()``. See file header for the read/process/save
    # invariant we're guarding.
    matrix_since_token: str = ""
    # ──────────────────────────────────────────────────────────────────────
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_public_dict(self) -> dict[str, Any]:
        """Sanitised view safe for API responses / logs (NO tokens)."""
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
            # Matrix transport surface — device_id + since_token are safe
            # to expose (device_id is public server-side, since_token is
            # an opaque cursor with no auth power). matrix_access_token
            # is NOT included — it's the secret.
            "matrix_device_id": self.matrix_device_id,
            "matrix_has_since_token": bool(self.matrix_since_token),
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
        """Insert or update one agent's credential row (secrets base64-encoded).

        Do NOT call this on every /sync tick to persist ``matrix_since_token``
        — use ``update_since_token()`` instead. This method rewrites the
        whole row and is meant for bind / unbind / owner-update flows.
        """
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
            # Matrix transport fields — populated on bind, hydrated on read.
            "matrix_access_token_encoded": _encode_token(cred.matrix_access_token),
            "matrix_device_id": cred.matrix_device_id,
            "matrix_since_token": cred.matrix_since_token,
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
        """All enabled rows — consumed by :class:`MatrixTrigger`'s
        credential watcher.

        Since Commit 7 (2026-07-02), Matrix is the only NarraMessenger
        transport, so no ``connection_mode`` filter here. Pre-Matrix
        ``gateway`` rows still exist in the DB but load without a
        ``matrix_access_token`` — MatrixTrigger.connect raises on the
        missing token and the base flips ``enabled=False``, so those
        rows drop out on the first pass and the owner has to re-run the
        bind flow to end up on Matrix.
        """
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

    async def update_since_token(
        self, agent_id: str, since_token: str
    ) -> None:
        """Persist the /sync cursor after we've processed a sync response.

        High-frequency write (once per sync round-trip, i.e. every few
        seconds when a room is chatty). Deliberately narrow: touches
        ``matrix_since_token`` only, not ``updated_at`` — so busy rooms
        don't spam the ``updated_at`` column and drown human-meaningful
        edits in the timestamp channel. If you need the last-active
        timestamp separately, add a dedicated column.

        Ordering invariant (see [[matrix_trigger.py]] design):
          resp = client.sync(since=X)
          for event in resp.events: process(event)   # ← FIRST
          update_since_token(agent_id, resp.next_batch)   # ← ONLY IF above succeeds

        A crash inside ``process()`` leaves the cursor at X; on restart
        the server re-plays the batch and our event handler dedups by
        event_id at the trigger's base pipeline. Never save the cursor
        BEFORE processing, or a crashed batch is lost forever.
        """
        await self._db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"matrix_since_token": since_token},
        )

    async def update_device_id(self, agent_id: str, device_id: str) -> None:
        """Called once, on the first ``sync`` after a fresh bind when
        matrix-nio auto-registered a device (empty ``device_id`` on the row).
        We pin it so future syncs reuse the same server-side device instead
        of spawning a new one on every restart."""
        await self._db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"matrix_device_id": device_id, "updated_at": self._now_iso()},
        )

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
            matrix_access_token=_decode_token(
                row.get("matrix_access_token_encoded", "")
            ),
            matrix_device_id=row.get("matrix_device_id", "") or "",
            matrix_since_token=row.get("matrix_since_token", "") or "",
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
