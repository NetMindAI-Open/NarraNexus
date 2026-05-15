"""
@file_name: _slack_credential_manager.py
@date: 2026-05-08
@description: CRUD for `channel_slack_credentials` table.

One row per agent. ``bind`` validates tokens via Slack ``auth.test`` before
persisting and back-fills ``team_id`` / ``team_name`` / ``bot_user_id`` from
the response. Tokens are base64-encoded at rest (mirrors lark_credentials —
NOT real encryption; production deployments should swap in KMS).
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.utils.database import AsyncDatabaseClient

from .slack_sdk_client import SlackSDKClient, SlackSDKError


def _encode_token(raw: str) -> str:
    if not raw:
        return ""
    return base64.b64encode(raw.encode()).decode()


def _decode_token(encoded: str) -> str:
    if not encoded:
        return ""
    return base64.b64decode(encoded.encode()).decode()


@dataclass
class SlackCredential:
    """One agent's Slack workspace binding."""

    agent_id: str
    bot_token: str  # decoded — caller-side use only, never log
    app_token: str  # decoded
    bot_user_id: str = ""
    team_id: str = ""
    team_name: str = ""
    # Owner — populated at bind via users.lookupByEmail. Empty when the
    # binder didn't supply an email (or lookup failed); the agent then
    # has no trust signal and treats every Slack sender as untrusted.
    owner_email: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_public_dict(self) -> dict[str, Any]:
        """Sanitised view safe for API responses / logs (NO tokens)."""
        return {
            "agent_id": self.agent_id,
            "bot_user_id": self.bot_user_id,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "owner_email": self.owner_email,
            "owner_user_id": self.owner_user_id,
            "owner_name": self.owner_name,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SlackCredentialManager:
    """Manages per-agent Slack credentials in `channel_slack_credentials`."""

    TABLE = "channel_slack_credentials"

    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def bind(
        self,
        agent_id: str,
        bot_token: str,
        app_token: str,
        owner_email: str = "",
    ) -> dict[str, Any]:
        """Validate tokens via auth.test, optionally resolve owner, upsert.

        ``owner_email`` is optional — when provided, we run
        ``users.lookupByEmail`` so the agent can later distinguish "owner
        is interacting" from "stranger is interacting". A failed lookup
        does NOT fail the bind (the bot still works, just without the
        trust signal); we log a warning and proceed with empty owner
        fields.

        Returns ``{success, error?, data?}``. ``data`` carries
        ``team_id``, ``team_name``, ``bot_user_id``, and (if resolved)
        ``owner_user_id`` / ``owner_name``.
        """
        bot_token = (bot_token or "").strip()
        app_token = (app_token or "").strip()
        owner_email = (owner_email or "").strip()

        if not bot_token.startswith("xoxb-"):
            return {"success": False, "error": "bot_token must start with 'xoxb-'"}
        if not app_token.startswith("xapp-"):
            return {"success": False, "error": "app_token must start with 'xapp-'"}

        # Validate via Slack auth.test — establishes token works AND surfaces bot identity
        client = SlackSDKClient(bot_token)
        try:
            auth = await client.auth_test()
        except SlackSDKError as e:
            # Lazy import to avoid a circular reference (the service module
            # imports this manager).
            from ._slack_service import _friendly_slack_error
            return {"success": False, "error": _friendly_slack_error(e.code or "")}

        team_id = auth.get("team_id", "")
        team_name = auth.get("team", "")
        bot_user_id = auth.get("user_id", "")

        if not bot_user_id:
            return {"success": False, "error": "auth.test returned no bot user_id"}

        # Bot-uniqueness check: same (team_id, bot_user_id) tuple may be
        # bound to AT MOST one agent. The DB has a UNIQUE INDEX as the
        # final guard against concurrent races; this check makes the
        # error message friendly when the user (not concurrent code)
        # tries to bind the same bot twice.
        existing_other = await self._db.get_one(
            self.TABLE, {"team_id": team_id, "bot_user_id": bot_user_id}
        )
        if existing_other and existing_other.get("agent_id") != agent_id:
            return {
                "success": False,
                "error": (
                    f"This Slack bot ({bot_user_id} in {team_name or team_id}) "
                    f"is already bound to another agent "
                    f"({existing_other.get('agent_id')}). Each Slack bot can "
                    f"only serve one agent — create a separate Slack app for "
                    f"this agent, or unbind the bot from the other agent first."
                ),
            }

        # Optionally resolve owner identity by email. Wrap in a short
        # timeout — Slack's users.lookupByEmail is Tier 3 (rate-limited)
        # and can take ~200-800 ms normally, but tail latency / rate
        # limiting can stretch it past 5-10s and block the bind round
        # trip. Failure here is non-fatal (binding still works without
        # the owner trust signal), so cap the wait and degrade gracefully.
        owner_user_id = ""
        owner_name = ""
        if owner_email:
            try:
                owner = await asyncio.wait_for(
                    client.lookup_user_by_email(owner_email), timeout=3.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[slack:{agent_id}] users.lookupByEmail timed out for "
                    f"{owner_email}; binding proceeds without owner trust signal"
                )
                owner = None
            if owner:
                owner_user_id = owner.get("id", "") or ""
                owner_name = (
                    owner.get("real_name")
                    or owner.get("profile", {}).get("display_name")
                    or owner.get("name")
                    or ""
                )
            else:
                logger.warning(
                    f"[slack:{agent_id}] users.lookupByEmail failed for "
                    f"{owner_email}; binding proceeds without owner trust signal"
                )

        # Upsert
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        now_iso = self._now_iso()
        row = {
            "agent_id": agent_id,
            "bot_token_encoded": _encode_token(bot_token),
            "app_token_encoded": _encode_token(app_token),
            "bot_user_id": bot_user_id,
            "team_id": team_id,
            "team_name": team_name,
            "owner_email": owner_email,
            "owner_user_id": owner_user_id,
            "owner_name": owner_name,
            "enabled": 1,
            "updated_at": now_iso,
        }
        if existing:
            await self._db.update(self.TABLE, {"agent_id": agent_id}, row)
            logger.info(f"[slack:{agent_id}] credentials updated, team={team_name}, owner={owner_name or '-'}")
        else:
            row["created_at"] = now_iso
            await self._db.insert(self.TABLE, row)
            logger.info(f"[slack:{agent_id}] credentials inserted, team={team_name}, owner={owner_name or '-'}")

        return {
            "success": True,
            "data": {
                "team_id": team_id,
                "team_name": team_name,
                "bot_user_id": bot_user_id,
                "owner_user_id": owner_user_id,
                "owner_name": owner_name,
            },
        }

    async def get(self, agent_id: str) -> Optional[SlackCredential]:
        """Fetch credential by agent_id (decoded). Returns None if missing."""
        row = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not row:
            return None
        return self._row_to_cred(row)

    async def get_public(self, agent_id: str) -> Optional[dict[str, Any]]:
        """Fetch sanitised credential view (no raw tokens)."""
        cred = await self.get(agent_id)
        return cred.to_public_dict() if cred else None

    async def unbind(self, agent_id: str) -> bool:
        """Remove credential row. Returns True if a row was removed."""
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.delete(self.TABLE, {"agent_id": agent_id})
        logger.info(f"[slack:{agent_id}] credentials unbound")
        return True

    async def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        """Flip ``enabled`` flag without deleting the row.

        Used by the trigger to disable a credential after detecting a
        permanent auth failure (e.g. ``invalid_auth`` / ``token_revoked``)
        so the watcher stops respawning subscribers against a dead token.
        User can re-bind to re-enable.
        """
        existing = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        if not existing:
            return False
        await self._db.update(
            self.TABLE, {"agent_id": agent_id}, {"enabled": 1 if enabled else 0},
        )
        return True

    async def list_active(self) -> list[SlackCredential]:
        """All enabled credentials. Used by SlackTrigger's credential watcher."""
        rows = await self._db.get(self.TABLE, {"enabled": 1})
        return [self._row_to_cred(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_cred(self, row: dict[str, Any]) -> SlackCredential:
        return SlackCredential(
            agent_id=row.get("agent_id", ""),
            bot_token=_decode_token(row.get("bot_token_encoded", "")),
            app_token=_decode_token(row.get("app_token_encoded", "")),
            bot_user_id=row.get("bot_user_id", "") or "",
            team_id=row.get("team_id", "") or "",
            team_name=row.get("team_name", "") or "",
            owner_email=row.get("owner_email", "") or "",
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
