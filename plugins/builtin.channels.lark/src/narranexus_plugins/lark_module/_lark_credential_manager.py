"""
@file_name: _lark_credential_manager.py
@date: 2026-04-10
@description: CRUD operations for lark_credentials table.

Stores per-agent Lark/Feishu bot binding information. App Secret is stored
both in lark-cli Keychain (for CLI tools) and encrypted in DB (for SDK trigger).
"""

from __future__ import annotations

import base64
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
    # The bot's own Lark open_id (from /open-apis/bot/v3/info). Drives the
    # group-message @-mention gate in lark_trigger: without it we can only
    # fall back to display-name matching, which a same-named human breaks.
    bot_open_id: str = ""
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

    def to_raw_dict(self) -> dict[str, Any]:
        """RAW view INCLUDING app_secret_encoded (the base64 app secret). Lark
        has no to_public_dict; this is the full wire form the ChannelCredentialStore
        seam's owner-gated endpoint + the CLI/status tools consume. permission_state
        (the three-click flow blob) rides as-is; timestamps are emitted ISO when
        they are datetimes (MySQL) and passed through when already strings
        (SQLite — Lark's _row_to_credential does not normalise them). Never logged."""
        def _dt(v: Any) -> Any:
            return v.isoformat() if isinstance(v, datetime) else (v or None)

        return {
            "agent_id": self.agent_id,
            "app_id": self.app_id,
            "app_secret_ref": self.app_secret_ref,
            "brand": self.brand,
            "profile_name": self.profile_name,
            "workspace_path": self.workspace_path,
            "bot_name": self.bot_name,
            "bot_open_id": self.bot_open_id,
            "app_secret_encoded": self.app_secret_encoded,
            "owner_open_id": self.owner_open_id,
            "owner_name": self.owner_name,
            "auth_status": self.auth_status,
            "is_active": self.is_active,
            "permission_state": self.permission_state or {},
            "created_at": _dt(self.created_at),
            "updated_at": _dt(self.updated_at),
        }


def _cred_from_raw(raw: dict[str, Any]) -> "LarkCredential":
    """Inverse of to_raw_dict — rebuild the dataclass from the seam's raw dict so
    an HttpStore-backed lookup reconstructs a functionally-identical credential
    (app_id / app_secret_encoded / permission_state / auth_status — everything the
    Lark tools read). ISO timestamp strings are parsed back to datetimes."""
    def _dt(v: Any) -> Any:
        if isinstance(v, str) and v:
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None
        return v if isinstance(v, datetime) else None

    return LarkCredential(
        agent_id=raw.get("agent_id", "") or "",
        app_id=raw.get("app_id", "") or "",
        app_secret_ref=raw.get("app_secret_ref", "") or "",
        brand=raw.get("brand", "feishu") or "feishu",
        profile_name=raw.get("profile_name", "") or "",
        workspace_path=raw.get("workspace_path", "") or "",
        bot_name=raw.get("bot_name", "") or "",
        bot_open_id=raw.get("bot_open_id", "") or "",
        app_secret_encoded=raw.get("app_secret_encoded", "") or "",
        owner_open_id=raw.get("owner_open_id", "") or "",
        owner_name=raw.get("owner_name", "") or "",
        auth_status=raw.get("auth_status", AUTH_STATUS_NOT_LOGGED_IN) or AUTH_STATUS_NOT_LOGGED_IN,
        is_active=bool(raw.get("is_active", True)),
        permission_state=raw.get("permission_state") or {},
        created_at=_dt(raw.get("created_at")),
        updated_at=_dt(raw.get("updated_at")),
    )


def _store(db: Any):
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    return GenericCredentialStore(db)


CHANNEL = "lark"


class LarkCredentialManager:
    """CRUD for lark_credentials table."""

    # Persistence: the generic channel_credentials table (plugin platform batch 4d);
    # the historical lark_credentials table is retired, never dropped.
    TABLE = "lark_credentials"  # retired — kept for the legacy copy migration and diagnostics

    def __init__(self, db):
        self.db = db

    # The raw dict is the stored shape: app_secret_encoded (base64, the SDK's
    # working form) is the secret half — encrypted by the store — everything
    # else, permission_state included, is the public half; is_active maps to
    # the store's enabled flag.
    @staticmethod
    def _values(cred: LarkCredential) -> dict[str, Any]:
        raw = cred.to_raw_dict()
        for k in ("agent_id", "is_active", "created_at", "updated_at"):
            raw.pop(k, None)
        raw["permission_state"] = cred.permission_state or {}
        return raw

    @staticmethod
    def _from_record(record: Any) -> LarkCredential:
        raw = record.to_raw_dict()
        raw["is_active"] = record.enabled
        return _cred_from_raw(raw)

    async def get_credential(self, agent_id: str) -> Optional[LarkCredential]:
        """The agent's Lark binding (None when unbound)."""
        record = await _store(self.db).get(CHANNEL, agent_id)
        return self._from_record(record) if record else None

    async def get_by_app_id(self, app_id: str) -> List[LarkCredential]:
        """Every binding of a Lark app (an app may serve one agent; the list form keeps the old contract)."""
        record = await _store(self.db).find_one(CHANNEL, external_id=app_id)
        return [self._from_record(record)] if record else []

    async def get_active_credentials(self) -> List[LarkCredential]:
        """Active bindings whose bot identity works (the trigger's subscriber set)."""
        return [
            self._from_record(r)
            for r in await _store(self.db).list_active(CHANNEL)
            if r.public.get("auth_status") in AUTH_STATUSES_BOT_ACTIVE
        ]

    async def migrate_legacy_auth_status(self) -> int:
        """One-off: the pre-2026-05 ``logged_in`` status became ``bot_ready``."""
        store = _store(self.db)
        count = 0
        for record in await store.list_all(CHANNEL):
            if record.public.get("auth_status") == "logged_in":
                await store.patch(CHANNEL, record.agent_id, {"auth_status": AUTH_STATUS_BOT_READY})
                count += 1
        if count:
            logger.info(f"Migrated {count} lark credentials from 'logged_in' to 'bot_ready'")
        return count

    _PATCHABLE_FIELDS = frozenset({
        "app_id", "app_secret_ref", "app_secret_encoded", "brand", "profile_name", "workspace_path",
        "bot_name", "bot_open_id", "owner_open_id", "owner_name", "auth_status", "is_active", "permission_state",
    })

    async def save_credential(self, cred: LarkCredential) -> None:
        """Insert or replace the agent's binding."""
        await _store(self.db).upsert(CHANNEL, cred.agent_id, self._values(cred), enabled=bool(cred.is_active))
        logger.info(f"Saved Lark credential for agent {cred.agent_id} (app_id={cred.app_id})")

    async def apply_patch(self, agent_id: str, patch: dict[str, Any]) -> None:
        """Merge ``patch`` (nested dicts deep-merged: permission_state) into the stored credential.

        Field-level safety against a concurrent writer on a DISJOINT field
        (update_auth_status from the trigger while the panel patches
        permission_state) comes from the store's optimistic version: the merge
        is re-read and retried if the row moved.
        """
        from narranexus.platform.module_system.data_access.channel_store import deep_merge

        unknown = [k for k in patch if k not in self._PATCHABLE_FIELDS]
        if unknown:
            raise ValueError(f"apply_patch: unknown lark credential field {unknown[0]!r}")
        cred = await self.get_credential(agent_id)
        if cred is None:
            raise ValueError(f"no Lark credential to patch for agent {agent_id}")
        merged = deep_merge(cred.to_raw_dict(), patch)
        fields = {k: merged[k] for k in patch}
        enabled = bool(merged["is_active"]) if "is_active" in patch else None
        fields.pop("is_active", None)
        await _store(self.db).patch(CHANNEL, agent_id, fields, enabled=enabled)

    async def save_raw(self, agent_id: str, raw: dict[str, Any]) -> None:
        """Replace the whole credential from a raw dict; the path's ``agent_id`` pins the row (a body cannot retarget)."""
        await self.save_credential(_cred_from_raw({**raw, "agent_id": agent_id}))

    async def update_auth_status(self, agent_id: str, status: str) -> None:
        """The trigger's status writes (bot_ready / user_logged_in / expired / brand_mismatch)."""
        await _store(self.db).patch(CHANNEL, agent_id, {"auth_status": status})

    async def set_is_active(self, agent_id: str, is_active: bool) -> bool:
        """Flip the binding on/off without deleting it (bundle-imported credentials land inactive)."""
        return await _store(self.db).set_enabled(CHANNEL, agent_id, is_active)

    async def update_bot_identity(
        self, agent_id: str, bot_name: str = "", bot_open_id: str = ""
    ) -> None:
        data = {}
        if bot_name:
            data["bot_name"] = bot_name
        if bot_open_id:
            data["bot_open_id"] = bot_open_id
        if not data:
            return
        await _store(self.db).patch(CHANNEL, agent_id, data)

    async def update_owner(self, agent_id: str, open_id: str, name: str) -> None:
        await _store(self.db).patch(CHANNEL, agent_id, {"owner_open_id": open_id, "owner_name": name})

    async def delete_credential(self, agent_id: str) -> None:
        await _store(self.db).unbind(CHANNEL, agent_id)
        logger.info(f"Deleted Lark credential for agent {agent_id}")
