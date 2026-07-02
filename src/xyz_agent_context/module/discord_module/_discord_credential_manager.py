"""
@file_name: _discord_credential_manager.py
@date: 2026-06-16
@description: CRUD for `channel_discord_credentials` table.

One row per agent. ``bind`` validates the token via ``GET /users/@me``
and optionally resolves the owner's display name from a supplied numeric
Discord user id (snowflake).

Mirrors ``telegram_module/_telegram_credential_manager.py`` end-to-end.
Two deltas vs Telegram:
  1. Owner is supplied as a NUMERIC Discord user id, not an @username.
     Discord usernames are not stable identifiers and the REST API
     resolves users by id, so we key the trust signal on the id directly
     and resolve the display name eagerly at bind (no late-resolution
     dance like Telegram's first-DM handshake).
  2. No webhook teardown — Discord delivery is a Gateway WebSocket, not
     long-poll, so there is no webhook to clear.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.utils.database import AsyncDatabaseClient

from .discord_sdk_client import DiscordSDKClient, DiscordSDKError


def _encode_token(raw: str) -> str:
    if not raw:
        return ""
    return base64.b64encode(raw.encode()).decode()


def _decode_token(encoded: str) -> str:
    if not encoded:
        return ""
    return base64.b64decode(encoded.encode()).decode()


@dataclass
class DiscordCredential:
    """One agent's Discord bot binding."""

    agent_id: str
    bot_token: str  # decoded — caller-side use only, never log
    bot_user_id: str = ""
    bot_username: str = ""
    # Owner — the agent owner's numeric Discord user id, supplied at bind.
    # Empty when not provided; without it there is no trust signal and the
    # agent treats every Discord sender as untrusted.
    owner_user_id: str = ""
    owner_name: str = ""
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_public_dict(self) -> dict[str, Any]:
        """Sanitised view safe for API responses / logs (NO token)."""
        return {
            "agent_id": self.agent_id,
            "bot_user_id": self.bot_user_id,
            "bot_username": self.bot_username,
            "owner_user_id": self.owner_user_id,
            "owner_name": self.owner_name,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DiscordCredentialManager:
    """Manages per-agent Discord credentials in `channel_discord_credentials`."""

    TABLE = "channel_discord_credentials"

    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def bind(
        self,
        agent_id: str,
        bot_token: str,
        owner_user_id: str = "",
    ) -> dict[str, Any]:
        """Validate token via GET /users/@me, optionally resolve owner, upsert.

        Returns ``{success, error?, data?}``. ``data`` carries
        ``bot_user_id``, ``bot_username``, and (if resolved)
        ``owner_user_id`` / ``owner_name`` on success.
        """
        bot_token = (bot_token or "").strip()
        owner_user_id = (owner_user_id or "").strip()
        if not bot_token:
            return {"success": False, "error": "bot_token is required"}
        # Discord bot tokens have no single stable regex shape across eras
        # (legacy vs newer formats differ), so we skip prefix validation and
        # let GET /users/@me be the authority.
        if owner_user_id and not owner_user_id.isdigit():
            return {
                "success": False,
                "error": "owner_user_id must be a numeric Discord user id (enable Developer Mode → right-click your name → Copy User ID).",
            }

        client = DiscordSDKClient(bot_token)
        try:
            me = await client.get_bot_user()
        except DiscordSDKError as e:
            from ._discord_service import _friendly_discord_error
            return {"success": False, "error": _friendly_discord_error(e.code or "")}

        bot_user_id = str(me.get("id", ""))
        bot_username = me.get("global_name") or me.get("username", "") or ""
        if not bot_user_id:
            return {"success": False, "error": "GET /users/@me returned no bot id"}

        # Bot-uniqueness check (app-level — DB UNIQUE INDEX is the final guard
        # against concurrent races; this gives a friendly error first).
        existing_other = await self._db.get_one(self.TABLE, {"bot_user_id": bot_user_id})
        if existing_other and existing_other.get("agent_id") != agent_id:
            return {
                "success": False,
                "error": (
                    f"This Discord bot (@{bot_username}) is already bound to "
                    f"another agent ({existing_other.get('agent_id')}). Each "
                    f"Discord bot can only serve one agent — create a separate "
                    f"application/bot in the Developer Portal for this agent, or "
                    f"unbind the bot from the other agent first."
                ),
            }

        # Best-effort owner name resolution from the numeric id.
        owner_name = ""
        if owner_user_id:
            try:
                owner = await client.get_user(owner_user_id)
                owner_name = owner.get("global_name") or owner.get("username", "") or ""
            except DiscordSDKError as e:
                logger.info(
                    f"[discord:{agent_id}] get_user({owner_user_id}) returned "
                    f"{e.code} at bind — owner name unresolved; trust signal "
                    f"still works on the numeric id."
                )

        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        now_iso = self._now_iso()
        row = {
            "agent_id": agent_id,
            "bot_token_encoded": _encode_token(bot_token),
            "bot_user_id": bot_user_id,
            "bot_username": bot_username,
            "owner_user_id": owner_user_id,
            "owner_name": owner_name,
            "enabled": 1,
            "updated_at": now_iso,
        }
        if existing:
            await self._db.update(self.TABLE, {"agent_id": agent_id}, row)
            logger.info(
                f"[discord:{agent_id}] credentials updated, bot=@{bot_username}, "
                f"owner={owner_name or '-'}"
            )
        else:
            row["created_at"] = now_iso
            await self._db.insert(self.TABLE, row)
            logger.info(
                f"[discord:{agent_id}] credentials inserted, bot=@{bot_username}, "
                f"owner={owner_name or '-'}"
            )

        return {
            "success": True,
            "data": {
                "bot_user_id": bot_user_id,
                "bot_username": bot_username,
                "owner_user_id": owner_user_id,
                "owner_name": owner_name,
            },
        }

    async def get(self, agent_id: str) -> Optional[DiscordCredential]:
        """Fetch credential by agent_id (decoded). Returns None if missing."""
        row = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not row:
            return None
        return self._row_to_cred(row)

    async def get_public(self, agent_id: str) -> Optional[dict[str, Any]]:
        """Fetch sanitised credential view (no raw token)."""
        cred = await self.get(agent_id)
        return cred.to_public_dict() if cred else None

    async def unbind(self, agent_id: str) -> bool:
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.delete(self.TABLE, {"agent_id": agent_id})
        logger.info(f"[discord:{agent_id}] credentials unbound")
        return True

    async def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        """Flip ``enabled`` without deleting the row. Used by the trigger to
        break out of a reconnect loop against a revoked token (Discord
        ``unauthorized``), mirroring Slack / Telegram."""
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.update(
            self.TABLE, {"agent_id": agent_id}, {"enabled": 1 if enabled else 0}
        )
        return True

    async def list_active(self) -> list[DiscordCredential]:
        rows = await self._db.get(self.TABLE, {"enabled": 1})
        return [self._row_to_cred(r) for r in rows]

    async def update_bot_identity(
        self,
        agent_id: str,
        *,
        bot_username: str = "",
        bot_user_id: str = "",
    ) -> bool:
        """Refresh ``bot_username`` / ``bot_user_id`` after a successful Test.

        Owners can rename their bot in the Developer Portal post-bind.
        """
        updates: dict[str, Any] = {"updated_at": self._now_iso()}
        if bot_username:
            updates["bot_username"] = bot_username
        if bot_user_id:
            updates["bot_user_id"] = bot_user_id
        if len(updates) == 1:  # only timestamp
            return False
        affected = await self._db.update(self.TABLE, {"agent_id": agent_id}, updates)
        return bool(affected)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_cred(self, row: dict[str, Any]) -> DiscordCredential:
        return DiscordCredential(
            agent_id=row.get("agent_id", ""),
            bot_token=_decode_token(row.get("bot_token_encoded", "")),
            bot_user_id=row.get("bot_user_id", "") or "",
            bot_username=row.get("bot_username", "") or "",
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
