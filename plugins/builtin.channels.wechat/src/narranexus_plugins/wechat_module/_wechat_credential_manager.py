"""
@file_name: _wechat_credential_manager.py
@author:
@date: 2026-06-24
@description: CRUD for `channel_wechat_credentials` — one row per agent.

Mirrors ``telegram_module/_telegram_credential_manager.py``. Deltas vs Telegram:
  1. ``bind`` does NOT call the gateway to validate — the iLink ``bot_token`` is
     already proven by the QR-scan confirm (the route passes it post-confirm).
     So bind is a pure upsert of (bot_token, base_url, owner_user_id).
  2. The owner's WeChat id is opaque until they DM the freshly bound account,
     so it is claimed on the first inbound DM via ``claim_owner`` (compare-and-
     set on an empty ``owner_wx_id``) — Telegram resolves owner the same way
     (``update_owner``) because getChat won't take a user @handle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from narranexus.platform.utils.db.database import AsyncDatabaseClient




@dataclass
class WeChatCredential:
    """One agent's WeChat (iLink) binding."""

    agent_id: str
    bot_token: str  # decoded — caller-side use only, never log
    base_url: str = ""
    bot_wx_id: str = ""
    # Owner — owner_wx_id claimed on first DM; owner_user_id is the NarraNexus
    # account (agents.created_by) supplied at bind.
    owner_wx_id: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    enabled: bool = True
    # Why the platform switched the binding off (permanent upstream failure);
    # empty while enabled or when the owner disabled it by hand.
    disabled_reason: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_public_dict(self) -> dict[str, Any]:
        """Sanitised view safe for API responses / logs (NO token)."""
        return {
            "agent_id": self.agent_id,
            "base_url": self.base_url,
            "bot_wx_id": self.bot_wx_id,
            "owner_wx_id": self.owner_wx_id,
            "owner_user_id": self.owner_user_id,
            "owner_name": self.owner_name,
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_raw_dict(self) -> dict[str, Any]:
        """RAW view INCLUDING the decoded bot_token — distinct from to_public_dict.
        Only handed to the ChannelCredentialStore seam's owner-gated endpoint and
        the MCP send tools; never logged, never returned from a panel route."""
        return {**self.to_public_dict(), "bot_token": self.bot_token}


def _cred_from_raw(raw: dict[str, Any]) -> WeChatCredential:
    """Inverse of to_raw_dict — rebuild the dataclass from the seam's raw dict so
    an HttpStore-backed lookup reconstructs the SAME object DirectStore does."""
    return WeChatCredential(
        agent_id=raw.get("agent_id", "") or "",
        bot_token=raw.get("bot_token", "") or "",
        base_url=raw.get("base_url", "") or "",
        bot_wx_id=raw.get("bot_wx_id", "") or "",
        owner_wx_id=raw.get("owner_wx_id", "") or "",
        owner_user_id=raw.get("owner_user_id", "") or "",
        owner_name=raw.get("owner_name", "") or "",
        enabled=bool(raw.get("enabled", True)),
        disabled_reason=raw.get("disabled_reason", "") or "",
        created_at=WeChatCredentialManager._parse_dt(raw.get("created_at")),
        updated_at=WeChatCredentialManager._parse_dt(raw.get("updated_at")),
    )


def _store(db: Any):
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    return GenericCredentialStore(db)


CHANNEL = "wechat"


class WeChatCredentialManager:
    """Manages per-agent WeChat credentials in `channel_wechat_credentials`."""

    # Persistence: the generic channel_credentials table (plugin platform batch 4d);
    # the historical channel_wechat_credentials table is retired, never dropped.
    TABLE = "channel_wechat_credentials"  # retired — kept for the m0004 backfill and diagnostics

    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    async def bind(
        self,
        agent_id: str,
        bot_token: str,
        base_url: str = "",
        owner_user_id: str = "",
    ) -> dict[str, Any]:
        """Persist a confirmed iLink binding. Upsert; no network validation
        (the QR-scan confirm already proved the token). Returns
        ``{success, error?, data?}``."""
        bot_token = (bot_token or "").strip()
        if not bot_token:
            return {"success": False, "error": "bot_token is empty (QR bind not confirmed)"}

        store = _store(self._db)
        existing = await store.get(CHANNEL, agent_id)
        kept = existing.public if existing else {}
        # owner_wx_id stays the empty string (never absent) so the first-DM
        # claim_owner compare-and-set (owner_wx_id == "") can match this row.
        values = {
            "bot_token": bot_token,
            "base_url": base_url or "",
            "owner_user_id": owner_user_id or kept.get("owner_user_id", "") or "",
            "owner_wx_id": kept.get("owner_wx_id", "") or "",
            "bot_wx_id": kept.get("bot_wx_id", "") or "",
            "owner_name": kept.get("owner_name", "") or "",
        }
        await store.upsert(CHANNEL, agent_id, values, enabled=True)
        logger.info(f"[wechat:{agent_id}] credentials {'updated' if existing else 'inserted'}")
        return {"success": True, "data": {"base_url": values["base_url"]}}

    async def get(self, agent_id: str) -> Optional[WeChatCredential]:
        record = await _store(self._db).get(CHANNEL, agent_id)
        return _cred_from_raw(record.to_raw_dict()) if record else None

    async def get_public(self, agent_id: str) -> Optional[dict[str, Any]]:
        cred = await self.get(agent_id)
        return cred.to_public_dict() if cred else None

    async def unbind(self, agent_id: str) -> bool:
        if not await _store(self._db).unbind(CHANNEL, agent_id):
            return False
        logger.info(f"[wechat:{agent_id}] credentials unbound")
        return True

    async def set_enabled(self, agent_id: str, enabled: bool, *, reason: str = "") -> bool:
        """Flip ``enabled`` without deleting — the trigger uses this to stop
        reconnecting against a dead session (iLink getupdates ret!=0).
        ``reason`` lands in the public ``disabled_reason`` (cleared on enable)."""
        return await _store(self._db).set_enabled(CHANNEL, agent_id, enabled, reason=reason)

    async def list_active(self) -> list[WeChatCredential]:
        return [_cred_from_raw(r.to_raw_dict()) for r in await _store(self._db).list_active(CHANNEL)]

    async def claim_owner(self, agent_id: str, owner_wx_id: str) -> bool:
        """First-DM owner claim — compare-and-set on an empty ``owner_wx_id``.

        The owner's WeChat id is unknown at bind (the bind is owner-initiated
        from the Brain panel, but the wxid is opaque until they message). The
        first DM after binding claims owner. The CAS (filter requires
        ``owner_wx_id = ''``) means only the first DM wins and a re-bind is
        needed to re-open the claim. Returns True iff this call claimed it.
        """
        if not await _store(self._db).update_if(CHANNEL, agent_id, {"owner_wx_id": ""}, {"owner_wx_id": owner_wx_id}):
            return False
        logger.info(f"[wechat:{agent_id}] owner claimed via first DM: {owner_wx_id}")
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------


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
