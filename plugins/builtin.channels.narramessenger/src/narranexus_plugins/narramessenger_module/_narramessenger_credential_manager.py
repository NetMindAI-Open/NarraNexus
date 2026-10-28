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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from narranexus.platform.utils.db.database import AsyncDatabaseClient




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
    # Why the platform switched the binding off (permanent upstream failure);
    # empty while enabled or when the owner disabled it by hand.
    disabled_reason: str = ""
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
            "disabled_reason": self.disabled_reason,
            # Matrix transport surface — device_id + since_token are safe
            # to expose (device_id is public server-side, since_token is
            # an opaque cursor with no auth power). matrix_access_token
            # is NOT included — it's the secret.
            "matrix_device_id": self.matrix_device_id,
            "matrix_has_since_token": bool(self.matrix_since_token),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_raw_dict(self) -> dict[str, Any]:
        """RAW view INCLUDING both decoded secrets (bearer_token control-plane +
        matrix_access_token message-plane) AND the full matrix_since_token —
        distinct from to_public_dict, which drops the secrets and reduces the
        since-token to a bool. Explicit field list (NOT {**to_public_dict()})
        because the sanitised view is lossy. Only handed to the seam's
        owner-gated endpoint + the send/CLI tools; never logged."""
        return {
            "agent_id": self.agent_id,
            "bearer_token": self.bearer_token,
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
            "disabled_reason": self.disabled_reason,
            "matrix_access_token": self.matrix_access_token,
            "matrix_device_id": self.matrix_device_id,
            "matrix_since_token": self.matrix_since_token,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def _cred_from_raw(raw: dict[str, Any]) -> "NarramessengerCredential":
    """Inverse of to_raw_dict — rebuild the dataclass from the seam's raw dict so
    an HttpStore-backed lookup reconstructs the SAME object DirectStore does."""
    return NarramessengerCredential(
        agent_id=raw.get("agent_id", "") or "",
        bearer_token=raw.get("bearer_token", "") or "",
        backend_base_url=raw.get("backend_base_url", "") or "",
        matrix_homeserver_url=raw.get("matrix_homeserver_url", "") or "",
        matrix_user_id=raw.get("matrix_user_id", "") or "",
        nexus_principal_id=raw.get("nexus_principal_id", "") or "",
        nexus_profile_id=raw.get("nexus_profile_id", "") or "",
        bind_room_id=raw.get("bind_room_id", "") or "",
        owner_matrix_user_id=raw.get("owner_matrix_user_id", "") or "",
        owner_name=raw.get("owner_name", "") or "",
        connection_mode=raw.get("connection_mode", "gateway") or "gateway",
        enabled=bool(raw.get("enabled", True)),
        disabled_reason=raw.get("disabled_reason", "") or "",
        matrix_access_token=raw.get("matrix_access_token", "") or "",
        matrix_device_id=raw.get("matrix_device_id", "") or "",
        matrix_since_token=raw.get("matrix_since_token", "") or "",
        created_at=NarramessengerCredentialManager._parse_dt(raw.get("created_at")),
        updated_at=NarramessengerCredentialManager._parse_dt(raw.get("updated_at")),
    )


def _store(db: Any):
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    return GenericCredentialStore(db)


CHANNEL = "narramessenger"


class NarramessengerCredentialManager:
    """Manages per-agent credentials in ``channel_narramessenger_credentials``."""

    # Persistence: the generic channel_credentials table (plugin platform batch 4d);
    # the historical channel_narramessenger_credentials table is retired, never dropped.
    TABLE = "channel_narramessenger_credentials"  # retired — kept for the legacy copy migration and diagnostics

    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _values(cred: NarramessengerCredential) -> dict[str, Any]:
        raw = cred.to_raw_dict()
        for k in ("agent_id", "enabled", "created_at", "updated_at"):
            raw.pop(k, None)
        raw["connection_mode"] = cred.connection_mode or "gateway"
        return raw

    async def upsert(self, cred: NarramessengerCredential) -> None:
        """Insert or replace the agent's binding (the bind services build the dataclass)."""
        store = _store(self._db)
        existed = await store.get(CHANNEL, cred.agent_id) is not None
        await store.upsert(CHANNEL, cred.agent_id, self._values(cred), enabled=bool(cred.enabled))
        logger.info(
            f"[narramessenger:{cred.agent_id}] credential {'updated' if existed else 'inserted'} "
            f"(matrix_user_id={cred.matrix_user_id})"
        )

    async def get(self, agent_id: str) -> Optional[NarramessengerCredential]:
        """Decoded credential for ``agent_id`` (None when unbound)."""
        record = await _store(self._db).get(CHANNEL, agent_id)
        return _cred_from_raw(record.to_raw_dict()) if record else None

    async def get_public(self, agent_id: str) -> Optional[dict[str, Any]]:
        cred = await self.get(agent_id)
        return cred.to_public_dict() if cred else None

    async def get_by_matrix_user_id(
        self, matrix_user_id: str
    ) -> Optional[NarramessengerCredential]:
        """The binding whose Matrix user id is ``matrix_user_id`` (the channel-wide identity)."""
        if not matrix_user_id:
            return None
        record = await _store(self._db).find_one(CHANNEL, external_id=matrix_user_id)
        return _cred_from_raw(record.to_raw_dict()) if record else None

    async def get_by_profile_id(
        self, nexus_profile_id: str
    ) -> Optional[NarramessengerCredential]:
        """The binding whose NarraMessenger profile id is ``nexus_profile_id``."""
        if not nexus_profile_id:
            return None
        record = await _store(self._db).find_one(CHANNEL, nexus_profile_id=nexus_profile_id)
        return _cred_from_raw(record.to_raw_dict()) if record else None

    async def list_active(self) -> list[NarramessengerCredential]:
        """Every enabled binding (the trigger's subscriber set)."""
        return [_cred_from_raw(r.to_raw_dict()) for r in await _store(self._db).list_active(CHANNEL)]

    async def set_enabled(self, agent_id: str, enabled: bool, *, reason: str = "") -> bool:
        """Flip ``enabled`` without deleting the row (bundle-imported credentials
        land inactive). ``reason`` lands in the public ``disabled_reason``
        (cleared on enable)."""
        return await _store(self._db).set_enabled(CHANNEL, agent_id, enabled, reason=reason)

    async def update_owner(
        self, agent_id: str, owner_matrix_user_id: str, owner_name: str
    ) -> bool:
        """Record the owner's Matrix identity once known."""
        return await _store(self._db).patch(CHANNEL, agent_id, {"owner_matrix_user_id": owner_matrix_user_id, "owner_name": owner_name}) is not None

    async def unbind(self, agent_id: str) -> bool:
        if not await _store(self._db).unbind(CHANNEL, agent_id):
            return False
        logger.info(f"[narramessenger:{agent_id}] credential unbound")
        return True

    async def update_since_token(
        self, agent_id: str, since_token: str
    ) -> None:
        """Persist the Matrix sync token so a restart resumes where it left off."""
        await _store(self._db).patch(CHANNEL, agent_id, {"matrix_since_token": since_token})

    async def update_device_id(self, agent_id: str, device_id: str) -> None:
        """Persist the Matrix device id the login minted."""
        await _store(self._db).patch(CHANNEL, agent_id, {"matrix_device_id": device_id})

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
