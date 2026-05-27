"""
@file_name: _lark_credential_manager.py
@date: 2026-04-10
@description: CRUD operations for lark_credentials table.

Stores per-agent Lark/Feishu bot binding information. App Secret is stored
both in lark-cli Keychain (for CLI tools) and encrypted in DB (for SDK trigger).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

from loguru import logger


def _encode_secret(secret: str) -> str:
    """Encode secret for DB storage. Uses base64 for local/dev mode.

    WARNING: base64 is NOT encryption — it is trivially reversible.
    For production/cloud deployments, replace with cryptography.fernet
    using a key from LARK_SECRET_ENCRYPTION_KEY env var.
    """
    if not secret:
        return ""
    return base64.b64encode(secret.encode()).decode()


# Keep old name as alias for backward compat during transition
_encrypt_secret = _encode_secret


def _decode_secret(encoded: str) -> str:
    """Decode secret from DB storage."""
    if not encoded:
        return ""
    return base64.b64decode(encoded.encode()).decode()


# Keep old name as alias for backward compat during transition
_decrypt_secret = _decode_secret


# ── Auth status constants ────────────────────────────────────────────
AUTH_STATUS_NOT_LOGGED_IN = "not_logged_in"
AUTH_STATUS_BOT_READY = "bot_ready"            # Bot identity works, user OAuth not done
AUTH_STATUS_USER_LOGGED_IN = "user_logged_in"  # User completed OAuth, all features available
AUTH_STATUS_EXPIRED = "expired"                # Credential validation failed
# Set by lark_trigger when the WebSocket subscriber observes error
# 1000040351 ("Incorrect domain name") — the user selected one platform
# (Feishu/Lark) but the App ID is registered on the other. The bot is
# bound but WILL NOT receive any messages. Surfaced to the frontend so
# LarkConfig can render a "re-bind with correct brand" prompt; surfaced
# to the agent so it can tell the user when they complain.
AUTH_STATUS_BRAND_MISMATCH = "brand_mismatch"
# Statuses that mean "bot identity works, safe to start WebSocket trigger".
# brand_mismatch is INTENTIONALLY excluded — restarting the trigger only
# triggers the same domain-mismatch error in a hot loop. The bind has to
# be redone with the correct brand to recover.
AUTH_STATUSES_BOT_ACTIVE = {AUTH_STATUS_BOT_READY, AUTH_STATUS_USER_LOGGED_IN}


@dataclass
class LarkCredential:
    """One agent's Lark bot binding."""

    agent_id: str
    app_id: str
    app_secret_ref: str  # Keychain reference, e.g. "appsecret:cli_xxx"
    brand: str  # "feishu" or "lark"
    profile_name: str  # CLI profile name, e.g. "agent_{agent_id}"
    workspace_path: str = ""  # HOME-based workspace directory
    bot_name: str = ""
    app_secret_encoded: str = ""  # Base64-encoded secret for SDK use (NOT encrypted)
    owner_open_id: str = ""  # Lark open_id of the agent's owner
    owner_name: str = ""  # Display name of the owner
    auth_status: str = AUTH_STATUS_NOT_LOGGED_IN  # not_logged_in / bot_ready / user_logged_in / expired
    is_active: bool = True
    # Free-form JSON blob tracking the three-click authorization flow.
    # Keys (see spec 2026-04-22-lark-three-click-auth-design §5.2):
    #   Click 2 — submit scope request to admin
    #     admin_request_url          — verification URL (user clicks to submit)
    #     admin_request_device_code  — bound to Click 2 URL; NEVER poll-able
    #     admin_request_generated_at — ISO
    #   Admin approval (self-reported by user; no API to query)
    #     admin_approved_at          — ISO, when user said "admin approved"
    #   Click 3 — user personal authorization
    #     user_authz_url             — fresh URL minted after admin_approved
    #     user_authz_device_code     — poll-able code for Click 3
    #     user_authz_generated_at    — ISO
    #   Completion (set when user_authz_device_code successfully exchanges for token)
    #     user_oauth_completed_at    — ISO timestamp (success)
    #     user_scopes_granted        — list[str]
    #     bot_scopes_confirmed       — bool, auto-flipped on completion
    #     console_setup_done_at      — ISO, auto-flipped on completion
    #   Optional
    #     availability_confirmed     — bool (user confirms app visible to org)
    permission_state: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def get_app_secret(self) -> str:
        """Decode and return the app secret."""
        return _decode_secret(self.app_secret_encoded)

    def receive_enabled(self) -> bool:
        """Can the LarkTrigger SDK subscriber start for this credential?"""
        return bool(self.app_secret_encoded)

    def user_oauth_ok(self) -> bool:
        """Has user authorization been completed (Click 3 tokens minted)?"""
        return bool(self.permission_state.get("user_oauth_completed_at"))

    def current_click_stage(self) -> str:
        """Where we are in the three-click authorization flow.

        Returns one of:
          'not_started'       — no admin request URL yet
          'waiting_admin'     — Click 2 URL generated, user may have clicked,
                                but admin hasn't approved (or we don't know yet)
          'waiting_user_click'— admin approved, Click 3 URL ready, user hasn't
                                clicked (or click hasn't been polled yet)
          'completed'         — user authorization minted a token

        Derivation strictly by DB fields, not by user's literal words.
        """
        ps = self.permission_state or {}
        if ps.get("user_oauth_completed_at"):
            return "completed"
        if ps.get("user_authz_url"):
            return "waiting_user_click"
        if ps.get("admin_request_url"):
            return "waiting_admin"
        return "not_started"


class LarkCredentialManager:
    """CRUD for lark_credentials table."""

    TABLE = "lark_credentials"

    def __init__(self, db):
        self.db = db

    def _row_to_credential(self, row: dict) -> LarkCredential:
        raw_state = row.get("permission_state") or "{}"
        if isinstance(raw_state, str):
            try:
                state = json.loads(raw_state) if raw_state.strip() else {}
            except json.JSONDecodeError:
                state = {}
        elif isinstance(raw_state, dict):
            state = raw_state
        else:
            state = {}

        return LarkCredential(
            agent_id=row["agent_id"],
            app_id=row["app_id"],
            app_secret_ref=row.get("app_secret_ref", ""),
            brand=row.get("brand", "feishu"),
            profile_name=row.get("profile_name", ""),
            workspace_path=row.get("workspace_path", ""),
            bot_name=row.get("bot_name", ""),
            app_secret_encoded=row.get("app_secret_encrypted", ""),
            owner_open_id=row.get("owner_open_id", ""),
            owner_name=row.get("owner_name", ""),
            auth_status=row.get("auth_status", "not_logged_in"),
            is_active=bool(row.get("is_active", True)),
            permission_state=state,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    async def get_credential(self, agent_id: str) -> Optional[LarkCredential]:
        """Get credential for a single agent."""
        row = await self.db.get_one(self.TABLE, {"agent_id": agent_id})
        if not row:
            return None
        return self._row_to_credential(row)

    async def get_by_app_id(self, app_id: str) -> List[LarkCredential]:
        """Get all credentials using a specific App ID."""
        rows = await self.db.get(self.TABLE, {"app_id": app_id})
        return [self._row_to_credential(r) for r in rows]

    async def get_active_credentials(self) -> List[LarkCredential]:
        """Get all active credentials with working bot identity (for trigger).

        Returns credentials with auth_status in {bot_ready, user_logged_in}.
        """
        rows = await self.db.get(self.TABLE, {"is_active": 1})
        return [
            self._row_to_credential(r) for r in rows
            if r.get("auth_status") in AUTH_STATUSES_BOT_ACTIVE
        ]

    async def migrate_legacy_auth_status(self) -> int:
        """Migrate old 'logged_in' rows to 'bot_ready'.

        Called on startup to handle DB rows created before the 4-state model.
        Conservative: we cannot know if user OAuth was completed, so downgrade
        to bot_ready. Users will need to re-do OAuth (one-time inconvenience).
        Returns number of rows migrated.
        """
        rows = await self.db.get(self.TABLE, {"auth_status": "logged_in"})
        count = 0
        for row in rows:
            await self.db.update(
                self.TABLE,
                {"agent_id": row["agent_id"]},
                {"auth_status": AUTH_STATUS_BOT_READY},
            )
            count += 1
        if count:
            logger.info(f"Migrated {count} lark_credentials from 'logged_in' to 'bot_ready'")
        return count

    async def save_credential(self, cred: LarkCredential) -> None:
        """Insert or update a credential."""
        data = {
            "agent_id": cred.agent_id,
            "app_id": cred.app_id,
            "app_secret_ref": cred.app_secret_ref,
            "app_secret_encrypted": cred.app_secret_encoded,
            "brand": cred.brand,
            "profile_name": cred.profile_name,
            "workspace_path": cred.workspace_path,
            "bot_name": cred.bot_name,
            "owner_open_id": cred.owner_open_id,
            "owner_name": cred.owner_name,
            "auth_status": cred.auth_status,
            "is_active": 1 if cred.is_active else 0,
            "permission_state": json.dumps(cred.permission_state or {}),
        }
        existing = await self.get_credential(cred.agent_id)
        if existing:
            await self.db.update(self.TABLE, {"agent_id": cred.agent_id}, data)
        else:
            await self.db.insert(self.TABLE, data)
        logger.info(f"Saved Lark credential for agent {cred.agent_id} (app_id={cred.app_id})")

    async def patch_permission_state(self, agent_id: str, updates: dict[str, Any]) -> dict:
        """Merge `updates` into the stored JSON permission_state and save.

        Read-modify-write by design — the same agent rarely has concurrent
        permission-config writers. Returns the merged state.
        """
        cred = await self.get_credential(agent_id)
        current = dict(cred.permission_state) if cred else {}
        current.update(updates)
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"permission_state": json.dumps(current)},
        )
        return current

    async def update_auth_status(self, agent_id: str, status: str) -> None:
        """Update authentication status."""
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"auth_status": status},
        )

    async def update_bot_name(self, agent_id: str, bot_name: str) -> None:
        """Update bot display name (after successful auth)."""
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"bot_name": bot_name},
        )

    async def update_owner(self, agent_id: str, open_id: str, name: str) -> None:
        """Update owner Lark identity."""
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"owner_open_id": open_id, "owner_name": name},
        )

    async def update_workspace_path(self, agent_id: str, workspace_path: str) -> None:
        """Set the DB's workspace_path for an agent.

        Used for lazy migration: pre-refactor manual binds had
        workspace_path="" and relied on shared ~/.lark-cli/ config. On
        first agent-scoped lark-cli call after the refactor, we compute
        the workspace path, persist it, and hydrate the workspace.
        """
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"workspace_path": workspace_path},
        )

    async def set_app_secret_encoded(self, agent_id: str, plain_secret: str) -> None:
        """Store a base64-encoded copy of the plain app secret.

        Needed for the SDK-based LarkTrigger subscriber, which requires the
        plain secret at `lark.ws.Client` construction time. For agent-
        assisted setups, this is populated via `lark_enable_receive` after
        the user pastes the secret from the Lark developer console.
        """
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"app_secret_encrypted": _encode_secret(plain_secret)},
        )
        logger.info(
            f"Stored app_secret_encoded for agent {agent_id} — "
            f"LarkTrigger subscriber will start on next watcher tick."
        )

    async def update_app_credentials(
        self,
        agent_id: str,
        app_id: str,
        app_secret_ref: str,
        is_active: bool = True,
        auth_status: str = "bot_ready",
    ) -> None:
        """Finalize a pending credential after agent-assisted setup completes.

        Called by _finalize_setup once `config init --new` exits successfully.
        We can read app_id and the keychain reference (app_secret_ref) from
        the CLI-written config.json, but the plain secret stays in the
        system keychain — so app_secret_encoded remains empty for this path.
        """
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {
                "app_id": app_id,
                "app_secret_ref": app_secret_ref,
                "is_active": 1 if is_active else 0,
                "auth_status": auth_status,
            },
        )
        logger.info(
            f"Finalized Lark credential for agent {agent_id} "
            f"(app_id={app_id}, is_active={is_active})"
        )

    async def delete_credential(self, agent_id: str) -> None:
        """Delete credential for an agent."""
        await self.db.delete(self.TABLE, {"agent_id": agent_id})
        logger.info(f"Deleted Lark credential for agent {agent_id}")
