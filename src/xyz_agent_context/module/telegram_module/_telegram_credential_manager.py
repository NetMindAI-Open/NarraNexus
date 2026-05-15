"""
@file_name: _telegram_credential_manager.py
@date: 2026-05-09
@description: CRUD for `channel_telegram_credentials` table.

One row per agent. ``bind`` validates the token via ``getMe`` and (defensively)
calls ``deleteWebhook`` first so subsequent long-poll won't 409. Optionally
resolves owner identity via ``getChat("@handle")`` so the agent can later
distinguish owner from stranger.

Mirrors ``slack_module/_slack_credential_manager.py`` end-to-end. Three
deltas vs Slack:
  1. No ``team_id`` — Telegram is single-tenant per bot. Uniqueness keys on
     ``bot_user_id`` alone.
  2. Owner resolution via ``getChat("@handle")`` (no email lookup in
     Telegram Bot API).
  3. Single token (``bot_token``), not two.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.utils.database import AsyncDatabaseClient

from .telegram_sdk_client import TelegramSDKClient, TelegramSDKError


# Telegram bot tokens have a stable shape: ``<digits>:<35+ char base64>``.
# The previous check only verified the colon was present, so ``"1:x"``
# was accepted and produced a confusing ``Unauthorized`` from getMe.
# This regex catches obvious typos at the boundary, but the real
# validation is still getMe.
_TELEGRAM_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")


def _encode_token(raw: str) -> str:
    if not raw:
        return ""
    return base64.b64encode(raw.encode()).decode()


def _decode_token(encoded: str) -> str:
    if not encoded:
        return ""
    return base64.b64decode(encoded.encode()).decode()


@dataclass
class TelegramCredential:
    """One agent's Telegram bot binding."""

    agent_id: str
    bot_token: str  # decoded — caller-side use only, never log
    bot_user_id: str = ""
    bot_username: str = ""
    # Owner — populated at bind via getChat("@handle"). Empty when not provided
    # (or lookup failed); without this the trust signal is unavailable.
    owner_username: str = ""
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
            "owner_username": self.owner_username,
            "owner_user_id": self.owner_user_id,
            "owner_name": self.owner_name,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TelegramCredentialManager:
    """Manages per-agent Telegram credentials in `channel_telegram_credentials`."""

    TABLE = "channel_telegram_credentials"

    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def bind(
        self,
        agent_id: str,
        bot_token: str,
        owner_username: str = "",
    ) -> dict[str, Any]:
        """Validate token via getMe, optionally resolve owner, upsert.

        Returns ``{success, error?, data?}``. ``data`` carries
        ``bot_user_id``, ``bot_username``, and (if resolved)
        ``owner_user_id`` / ``owner_name`` on success.
        """
        bot_token = (bot_token or "").strip()
        owner_username = (owner_username or "").strip().lstrip("@")

        # Telegram tokens look like ``123456789:AAH-...`` — digits, colon, base64ish.
        # The regex is intentionally loose enough to accept any real token while
        # catching obvious typos / pasted partial strings before we waste a
        # network round trip on getMe.
        if not _TELEGRAM_TOKEN_RE.match(bot_token):
            return {
                "success": False,
                "error": "bot_token format looks wrong. Expected '<digits>:<base64>' (e.g. 7981632450:AAH-…).",
            }

        client = TelegramSDKClient(bot_token)
        try:
            # Defensive: clear any pre-existing webhook so long-poll won't 409 later.
            try:
                await client.delete_webhook()
            except TelegramSDKError as e:
                logger.warning(
                    f"[telegram:{agent_id}] deleteWebhook during bind failed: {e.code} "
                    f"(continuing — getUpdates may 409)"
                )

            try:
                me = await client.get_me()
            except TelegramSDKError as e:
                # Lazy import — service module imports this manager.
                from ._telegram_service import _friendly_telegram_error
                return {
                    "success": False,
                    "error": _friendly_telegram_error(e.code or ""),
                }

            bot_user_id = str(me.get("id", ""))
            bot_username = me.get("username", "") or ""
            if not bot_user_id:
                return {"success": False, "error": "getMe returned no bot id"}

            # Bot-uniqueness check (app-level — DB UNIQUE INDEX is the final
            # guard against concurrent races; this gives a friendly error).
            existing_other = await self._db.get_one(
                self.TABLE, {"bot_user_id": bot_user_id}
            )
            if existing_other and existing_other.get("agent_id") != agent_id:
                return {
                    "success": False,
                    "error": (
                        f"This Telegram bot (@{bot_username}) is already bound "
                        f"to another agent ({existing_other.get('agent_id')}). "
                        f"Each Telegram bot can only serve one agent — create "
                        f"a separate bot via @BotFather for this agent, or "
                        f"unbind the bot from the other agent first."
                    ),
                }

            # Best-effort owner resolution at bind time. Telegram's getChat
            # API does NOT accept @username for regular user accounts —
            # only for supergroups, channels, and bots. So this call
            # almost always returns ``chat_not_found`` for a user
            # @handle. We try anyway because:
            #   1. If the @handle happens to be a channel/supergroup
            #      (unusual for "owner") we get a real hit.
            #   2. Future API surface might broaden — keep the path
            #      warm so we benefit automatically.
            # When it fails (the common case), bind succeeds with
            # owner_user_id empty; the canonical resolution path is
            # ``TelegramTrigger._process_message`` matching the first
            # inbound DM's ``from.username`` against the stored
            # ``owner_username``, then calling ``update_owner``.
            owner_user_id = ""
            owner_name = ""
            if owner_username:
                try:
                    owner = await client.get_chat(f"@{owner_username}")
                    owner_user_id = str(owner.get("id", "")) or ""
                    first = owner.get("first_name", "") or ""
                    last = owner.get("last_name", "") or ""
                    owner_name = " ".join(p for p in (first, last) if p)
                except TelegramSDKError as e:
                    # Expected for user @usernames. Trigger will resolve
                    # owner on first matching DM.
                    logger.info(
                        f"[telegram:{agent_id}] getChat(@{owner_username}) "
                        f"returned {e.code} at bind — expected for user "
                        f"accounts; owner will be resolved on first DM "
                        f"whose from.username matches."
                    )

            # Upsert
            existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
            now_iso = self._now_iso()
            row = {
                "agent_id": agent_id,
                "bot_token_encoded": _encode_token(bot_token),
                "bot_user_id": bot_user_id,
                "bot_username": bot_username,
                "owner_username": owner_username,
                "owner_user_id": owner_user_id,
                "owner_name": owner_name,
                "enabled": 1,
                "updated_at": now_iso,
            }
            if existing:
                await self._db.update(self.TABLE, {"agent_id": agent_id}, row)
                logger.info(
                    f"[telegram:{agent_id}] credentials updated, bot=@{bot_username}, "
                    f"owner={owner_name or '-'}"
                )
            else:
                row["created_at"] = now_iso
                await self._db.insert(self.TABLE, row)
                logger.info(
                    f"[telegram:{agent_id}] credentials inserted, bot=@{bot_username}, "
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
        finally:
            await client.close()

    async def get(self, agent_id: str) -> Optional[TelegramCredential]:
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
        logger.info(f"[telegram:{agent_id}] credentials unbound")
        return True

    async def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        """Flip ``enabled`` flag without deleting the row. See
        ``SlackCredentialManager.set_enabled`` for the rationale — used by
        the trigger to break out of a reconnect loop against a revoked
        token (Telegram ``Unauthorized``)."""
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.update(
            self.TABLE, {"agent_id": agent_id}, {"enabled": 1 if enabled else 0},
        )
        return True

    async def list_active(self) -> list[TelegramCredential]:
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

        Owners can rename their bot in @BotFather post-bind. Without this
        refresh the UI's "DM @{bot_username} once" hint can point to a
        non-existent handle.
        """
        updates: dict[str, Any] = {"updated_at": self._now_iso()}
        if bot_username:
            updates["bot_username"] = bot_username
        if bot_user_id:
            updates["bot_user_id"] = bot_user_id
        if len(updates) == 1:  # only timestamp
            return False
        affected = await self._db.update(
            self.TABLE, {"agent_id": agent_id}, updates,
        )
        return bool(affected)

    async def update_owner(
        self,
        agent_id: str,
        owner_user_id: str,
        owner_name: str,
    ) -> bool:
        """Late owner resolution — called by TelegramTrigger when an inbound
        DM arrives whose ``from.username`` matches the bind-time
        ``owner_username``. Telegram's getChat API doesn't accept @username
        for regular users (only for supergroups/channels), so we can't
        resolve the numeric user_id at bind time; the first matching DM is
        when the mapping becomes available.

        Compare-and-set: the update only fires when ``owner_user_id`` is
        still empty. If two DMs race (legitimate owner + attacker who
        squatted the username while the lock window was open), only one
        wins, and once an ``owner_user_id`` is set this method becomes a
        no-op until the user re-binds. Without the CAS the second write
        would silently overwrite the first.

        Returns True if the row was updated, False if no row exists OR
        owner was already resolved (caller can ignore False — it means
        "lock has already been consumed").
        """
        affected = await self._db.update(
            self.TABLE,
            {"agent_id": agent_id, "owner_user_id": ""},
            {
                "owner_user_id": owner_user_id,
                "owner_name": owner_name,
                "updated_at": self._now_iso(),
            },
        )
        if not affected:
            return False
        logger.info(
            f"[telegram:{agent_id}] owner late-resolved: "
            f"user_id={owner_user_id} name={owner_name!r}"
        )
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_cred(self, row: dict[str, Any]) -> TelegramCredential:
        return TelegramCredential(
            agent_id=row.get("agent_id", ""),
            bot_token=_decode_token(row.get("bot_token_encoded", "")),
            bot_user_id=row.get("bot_user_id", "") or "",
            bot_username=row.get("bot_username", "") or "",
            owner_username=row.get("owner_username", "") or "",
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
