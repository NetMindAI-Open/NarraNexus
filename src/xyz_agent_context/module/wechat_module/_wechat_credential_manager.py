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

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.utils.database import AsyncDatabaseClient


def _encode_token(raw: str) -> str:
    return base64.b64encode(raw.encode()).decode() if raw else ""


def _decode_token(encoded: str) -> str:
    return base64.b64decode(encoded.encode()).decode() if encoded else ""


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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class WeChatCredentialManager:
    """Manages per-agent WeChat credentials in `channel_wechat_credentials`."""

    TABLE = "channel_wechat_credentials"

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

        now_iso = self._now_iso()
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        row: dict[str, Any] = {
            "agent_id": agent_id,
            "bot_token_encoded": _encode_token(bot_token),
            "base_url": base_url or "",
            "owner_user_id": owner_user_id or (existing or {}).get("owner_user_id", "") or "",
            "enabled": 1,
            "updated_at": now_iso,
        }
        if existing:
            await self._db.update(self.TABLE, {"agent_id": agent_id}, row)
            logger.info(f"[wechat:{agent_id}] credentials updated")
        else:
            row["created_at"] = now_iso
            # owner_wx_id MUST be the empty string (never NULL) so the first-DM
            # claim_owner CAS (`WHERE owner_wx_id = ''`) can match this row.
            row.setdefault("owner_wx_id", "")
            row.setdefault("bot_wx_id", "")
            await self._db.insert(self.TABLE, row)
            logger.info(f"[wechat:{agent_id}] credentials inserted")
        return {"success": True, "data": {"base_url": row["base_url"]}}

    async def get(self, agent_id: str) -> Optional[WeChatCredential]:
        row = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        return self._row_to_cred(row) if row else None

    async def get_public(self, agent_id: str) -> Optional[dict[str, Any]]:
        cred = await self.get(agent_id)
        return cred.to_public_dict() if cred else None

    async def unbind(self, agent_id: str) -> bool:
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.delete(self.TABLE, {"agent_id": agent_id})
        logger.info(f"[wechat:{agent_id}] credentials unbound")
        return True

    async def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        """Flip ``enabled`` without deleting — the trigger uses this to stop
        reconnecting against a dead session (iLink getupdates ret!=0)."""
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.update(
            self.TABLE, {"agent_id": agent_id}, {"enabled": 1 if enabled else 0}
        )
        return True

    async def list_active(self) -> list[WeChatCredential]:
        rows = await self._db.get(self.TABLE, {"enabled": 1})
        return [self._row_to_cred(r) for r in rows]

    async def claim_owner(self, agent_id: str, owner_wx_id: str) -> bool:
        """First-DM owner claim — compare-and-set on an empty ``owner_wx_id``.

        The owner's WeChat id is unknown at bind (the bind is owner-initiated
        from the Brain panel, but the wxid is opaque until they message). The
        first DM after binding claims owner. The CAS (filter requires
        ``owner_wx_id = ''``) means only the first DM wins and a re-bind is
        needed to re-open the claim. Returns True iff this call claimed it.
        """
        affected = await self._db.update(
            self.TABLE,
            {"agent_id": agent_id, "owner_wx_id": ""},
            {"owner_wx_id": owner_wx_id, "updated_at": self._now_iso()},
        )
        if not affected:
            return False
        logger.info(f"[wechat:{agent_id}] owner claimed via first DM: {owner_wx_id}")
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_cred(self, row: dict[str, Any]) -> WeChatCredential:
        return WeChatCredential(
            agent_id=row.get("agent_id", ""),
            bot_token=_decode_token(row.get("bot_token_encoded", "")),
            base_url=row.get("base_url", "") or "",
            bot_wx_id=row.get("bot_wx_id", "") or "",
            owner_wx_id=row.get("owner_wx_id", "") or "",
            owner_user_id=row.get("owner_user_id", "") or "",
            owner_name=row.get("owner_name", "") or "",
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
