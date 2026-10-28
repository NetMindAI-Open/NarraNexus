"""
@file_name: credential_store.py
@author: Bin Liang
@date: 2026-09-04
@description: ``GenericCredentialStore`` — the one credential store every IM channel is served from (table ``channel_credentials``).

Values are split by the channel's ``CredentialSchema``: declared secret
fields (and anything that looks like one — token / secret / password / key —
unless the schema declares it public) go encrypted into ``secret_json``,
identity fields into ``public_json``, and the schema's ``external_id_field``
into the channel-wide unique ``external_id``. Every channel — the six
builtin managers and plugin channels through the generic routes — reads and
writes here (batch 4d); the retired per-channel tables are copied in once by
``credential_legacy``. The descriptor comes from the ``ingress.channels`` registry.
"""
from __future__ import annotations

import json

from loguru import logger
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from narranexus.contracts.channel import ChannelDescriptor, CredentialField
from narranexus.platform.channel.credential_codec import decode_secrets, encode_secrets
from narranexus.platform.utils import utc_now

TABLE = "channel_credentials"
_SECRET_LIKE = re.compile(r"(token|secret|password|passwd|api_key|apikey|private_key)", re.IGNORECASE)


class UnknownChannel(ValueError):
    pass


@dataclass(frozen=True)
class CredentialRecord:
    channel: str
    agent_id: str
    enabled: bool
    external_id: Optional[str]
    public: dict[str, Any] = field(default_factory=dict)
    secret: dict[str, Any] = field(default_factory=dict)
    created_at: Any = None
    updated_at: Any = None
    version: int = 0
    # Set when the stored secret could not be decrypted (rotated / lost
    # SKILL_SECRETS_KEY, corrupt row). ``secret`` is then EMPTY and the record
    # must never reach a transport as if it were a valid credential:
    # ``list_active`` drops it, single-record readers surface the message.
    secret_error: Optional[str] = None

    @property
    def readable(self) -> bool:
        return self.secret_error is None

    @property
    def app_id(self) -> str:
        """The subscriber key ChannelTriggerBase expects on a credential (external id, else the agent id)."""
        return self.external_id or self.agent_id

    def to_public_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "agent_id": self.agent_id, "enabled": self.enabled, "external_id": self.external_id, **self.public}

    def to_raw_dict(self) -> dict[str, Any]:
        return {**self.to_public_dict(), **self.secret}


def descriptor_for(channel: str, registries: Any = None) -> ChannelDescriptor:
    regs = registries
    if regs is None:
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        regs = KERNEL_REGISTRIES
    registry = regs.registry_for("ingress.channels")
    if channel not in registry:
        raise UnknownChannel(f"unknown channel: {channel!r}")
    return registry.get(channel)


def all_descriptors(registries: Any = None) -> tuple[ChannelDescriptor, ...]:
    """Every registered channel descriptor (builtin and plugin), registration order."""
    regs = registries
    if regs is None:
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        regs = KERNEL_REGISTRIES
    return tuple(entry.factory() for entry in regs.registry_for("ingress.channels").entries())


def split_values(descriptor: ChannelDescriptor, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Optional[str]]:
    """(public, secret, external_id) for ``values`` under the descriptor's schema."""
    schema = descriptor.credential_schema
    declared_secret = set(schema.secret_names())
    declared_public = set(schema.public_names())
    public: dict[str, Any] = {}
    secret: dict[str, Any] = {}
    for key, value in values.items():
        if key in ("channel", "agent_id", "enabled", "external_id", "id", "created_at", "updated_at"):
            continue
        if key in declared_secret or (key not in declared_public and _SECRET_LIKE.search(key)):
            secret[key] = value
        else:
            public[key] = value
    external = values.get(schema.external_id_field) if schema.external_id_field else None
    return public, secret, (str(external) if external not in (None, "") else None)


def missing_required(descriptor: ChannelDescriptor, values: dict[str, Any]) -> tuple[str, ...]:
    return tuple(f.name for f in descriptor.credential_schema.fields if f.required and not str(values.get(f.name, "") or "").strip())


def bind_fields_for(descriptor: ChannelDescriptor) -> tuple[CredentialField, ...]:
    """The fields a bind call accepts: the descriptor's ``bind_fields`` (manager-backed
    channels whose service takes something other than the stored shape) or the stored schema."""
    return descriptor.bind_fields or descriptor.credential_schema.fields


def validate_bind_fields(descriptor: ChannelDescriptor, values: dict[str, Any]) -> Optional[str]:
    """Fail-closed check of a bind body against ``bind_fields_for``: unknown names are
    rejected (they would reach a service's ``do_bind(**fields)``), required ones must be
    non-blank, a ``select`` must be one of its options. Returns the error text or None."""
    fields = bind_fields_for(descriptor)
    known = {f.name: f for f in fields}
    unknown = sorted(k for k in values if k not in known)
    if unknown:
        return f"unknown field(s): {', '.join(unknown)}"
    # Per-kind format checks — every channel gets them through its descriptor
    # rather than a channel-specific `if` in a route (the Lark bind used to
    # check "@" in owner_email by hand and lost it in the migration).
    for f in fields:
        raw = values.get(f.name)
        if raw in (None, ""):
            continue
        text = str(raw).strip()
        if f.kind == "email" and ("@" not in text or text.startswith("@") or text.endswith("@") or " " in text):
            return f"{f.name} must be an e-mail address"
        if f.kind == "url" and not (text.startswith("http://") or text.startswith("https://")):
            return f"{f.name} must be an http(s) URL"
        if f.kind == "int" and not text.lstrip("-").isdigit():
            return f"{f.name} must be an integer"
    missing = [f.name for f in fields if f.required and not str(values.get(f.name, "") or "").strip()]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"
    for f in fields:
        v = values.get(f.name)
        if f.kind == "select" and f.options and v not in (None, "") and str(v) not in f.options:
            return f"{f.name} must be one of: {', '.join(f.options)}"
    return None


# (channel, agent_id, version) triples already warned about — the watcher polls
# list_active every few seconds; one line per unreadable row version is the signal.
_UNREADABLE_WARNED: set[tuple[str, str, int]] = set()


class CredentialConflict(Exception):
    """A binding already claims this (channel, external id) or (channel, agent): the DB unique index is the enforcement point, this is its product-level shape."""

    def __init__(self, channel: str, external_id: Optional[str], agent_id: str) -> None:
        self.channel, self.external_id, self.agent_id = channel, external_id, agent_id
        super().__init__(f"{channel}: this bot ({external_id or agent_id}) is already bound to another agent")


def _is_integrity_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    text = str(exc)
    return "IntegrityError" in name or "UNIQUE constraint" in text or "Duplicate entry" in text


class GenericCredentialStore:
    def __init__(self, db: Any, registries: Any = None) -> None:
        self._db = db
        self._registries = registries

    def descriptor(self, channel: str) -> ChannelDescriptor:
        return descriptor_for(channel, self._registries)

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> CredentialRecord:
        public = json.loads(row.get("public_json") or "{}")
        # Decrypt per row: SecretBox.decrypt fails CLOSED (raises) on a token
        # this key cannot open. Letting that raise out of a list method turned
        # one bad row — possibly another user's — into a whole-channel outage
        # (the credential watcher retried forever). The record carries the
        # error instead; readers decide (list_active skips, get surfaces).
        secret: dict[str, Any] = {}
        secret_error: Optional[str] = None
        try:
            secret = decode_secrets(row.get("secret_json") or "")
        except Exception as exc:  # noqa: BLE001 — SecretDecryptError or a corrupt payload
            secret_error = f"credential unreadable ({type(exc).__name__}: {exc}) — re-bind required"
        return CredentialRecord(
            channel=row["channel"],
            agent_id=row["agent_id"],
            enabled=bool(row.get("enabled", 1)),
            external_id=row.get("external_id") or None,
            public=public if isinstance(public, dict) else {},
            secret=secret,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            version=int(row.get("version") or 0),
            secret_error=secret_error,
        )

    @staticmethod
    def _reenable_resets(descriptor: ChannelDescriptor) -> dict[str, Any]:
        """Values blanked on a disabled -> enabled transition (``CredentialSchema.reset_on_reenable``)."""
        return {name: "" for name in descriptor.credential_schema.reset_on_reenable}

    async def upsert(self, channel: str, agent_id: str, values: dict[str, Any], *, enabled: Optional[bool] = None) -> CredentialRecord:
        """Create or replace the (channel, agent) binding from a flat value dict (public + secret fields)."""
        descriptor = self.descriptor(channel)
        existing = await self._db.get_one(TABLE, {"channel": channel, "agent_id": agent_id})
        if enabled and existing and not bool(existing.get("enabled", 1)):
            # disabled -> enabled transition: stream state of the disabled period must not resume
            values = {**values, **self._reenable_resets(descriptor)}
        public, secret, external_id = split_values(descriptor, values)
        row = {
            "external_id": external_id,
            "public_json": json.dumps(public, sort_keys=True, default=str),
            "secret_json": encode_secrets(secret),
            "updated_at": utc_now(),
        }
        if enabled is not None:
            row["enabled"] = 1 if enabled else 0
        try:
            if existing:
                row["version"] = int(existing.get("version") or 0) + 1
                await self._db.update(TABLE, {"channel": channel, "agent_id": agent_id}, row)
            else:
                row.setdefault("enabled", 1)
                await self._db.insert(TABLE, {"channel": channel, "agent_id": agent_id, "created_at": utc_now(), "version": 0, **row})
        except Exception as exc:  # noqa: BLE001 — only the unique-index violation is translated
            # Two concurrent binds, or a bot already bound elsewhere that the
            # caller's find_one missed: the unique indexes on (channel, agent_id)
            # and (channel, external_id) decide, and the loser gets a typed error
            # instead of a driver message. No application-level pre-check — that
            # would be the race this translates.
            if _is_integrity_error(exc):
                raise CredentialConflict(channel, external_id, agent_id) from exc
            raise
        record = await self.get(channel, agent_id)
        assert record is not None
        return record

    async def patch(self, channel: str, agent_id: str, fields: dict[str, Any], *, expect: Optional[dict[str, Any]] = None, enabled: Optional[bool] = None) -> Optional[CredentialRecord]:
        """Merge ``fields`` into the stored values with optimistic concurrency.

        Re-reads and retries when the row's ``version`` moved under us, so two
        writers patching DISJOINT fields (the bind panel and a trigger's
        auth-status update) never clobber each other. ``expect`` is a
        compare-and-set on current values (``{"owner_user_id": ""}`` = only
        while unresolved). Returns the record, or None when the binding is
        missing or ``expect`` does not hold.
        """
        descriptor = self.descriptor(channel)
        for _ in range(8):
            raw = await self._db.get_one(TABLE, {"channel": channel, "agent_id": agent_id})
            if raw is None:
                return None
            current = self._row_to_record(raw)
            merged_values = {**current.public, **current.secret}
            if expect is not None and any(merged_values.get(k, "") != v for k, v in expect.items()):
                return None
            merged_values.update(fields)
            public, secret, external_id = split_values(descriptor, merged_values)
            if enabled and not current.enabled:
                # disabled -> enabled transition, in this same versioned write. Resets are
                # applied AFTER the split so they never count as "the caller supplied new
                # secrets" for the fail-closed guard below: on an unreadable row
                # (``current.secret`` is EMPTY) a secret-side reset would make ``secret``
                # non-empty and overwrite the original ciphertext. While unreadable the row
                # cannot connect (list_active skips it), so only its public-side resets apply;
                # if the key is later restored and the row is used as-is, its secret-side
                # values (e.g. a stale stream cursor) were never reset -- rebinding resets them.
                reset_public, reset_secret, _ = split_values(descriptor, self._reenable_resets(descriptor))
                public.update(reset_public)
                if not current.secret_error:
                    secret.update(reset_secret)
            row = {
                "external_id": external_id,
                "public_json": json.dumps(public, sort_keys=True, default=str),
                "updated_at": utc_now(),
                "version": current.version + 1,
            }
            # Fail-closed on an unreadable secret: ``current.secret`` is EMPTY
            # then, so re-encoding the merge would overwrite the stored
            # ciphertext with "" — a rotated/lost key would go from
            # "restore the key and the binding works again" to "every user
            # re-binds". Only a patch that itself carries a new secret value
            # may replace it; anything else (enabled flip, disabled_reason,
            # owner fields, auth status) leaves secret_json byte-identical.
            if current.secret_error and not secret:
                row["secret_json"] = raw.get("secret_json") or ""
            else:
                row["secret_json"] = encode_secrets(secret)
            if enabled is not None:
                row["enabled"] = 1 if enabled else 0
            affected = await self._db.update(TABLE, {"channel": channel, "agent_id": agent_id, "version": current.version}, row)
            if affected:
                return await self.get(channel, agent_id)
        raise RuntimeError(f"{channel}/{agent_id}: credential patch kept losing the version race")

    async def update(self, channel: str, agent_id: str, patch: dict[str, Any]) -> Optional[CredentialRecord]:
        """Merge ``patch`` into the stored values (missing binding → None)."""
        return await self.patch(channel, agent_id, patch)

    async def update_if(self, channel: str, agent_id: str, expect: dict[str, Any], fields: dict[str, Any]) -> bool:
        """Compare-and-set: apply ``fields`` only while ``expect`` holds; False when it does not (or no binding)."""
        return await self.patch(channel, agent_id, fields, expect=expect) is not None

    async def find_one(self, channel: str, *, external_id: Optional[str] = None, **public_equals: Any) -> Optional[CredentialRecord]:
        """The binding whose external id / public fields match (channel-wide lookups: 'is this bot already bound?')."""
        if external_id is not None:
            row = await self._db.get_one(TABLE, {"channel": channel, "external_id": external_id})
            if row is None:
                return None
            record = self._row_to_record(row)
            if all(record.public.get(k) == v for k, v in public_equals.items()):
                return record
            return None
        for record in await self.list_all(channel):
            if all(record.public.get(k) == v for k, v in public_equals.items()):
                return record
        return None

    async def get(self, channel: str, agent_id: str) -> Optional[CredentialRecord]:
        row = await self._db.get_one(TABLE, {"channel": channel, "agent_id": agent_id})
        return self._row_to_record(row) if row else None

    async def get_public(self, channel: str, agent_id: str) -> Optional[dict[str, Any]]:
        record = await self.get(channel, agent_id)
        return record.to_public_dict() if record else None

    async def unbind(self, channel: str, agent_id: str) -> bool:
        existing = await self._db.get_one(TABLE, {"channel": channel, "agent_id": agent_id})
        if not existing:
            return False
        await self._db.delete(TABLE, {"channel": channel, "agent_id": agent_id})
        return True

    async def set_enabled(self, channel: str, agent_id: str, enabled: bool, *, reason: str = "") -> bool:
        """Flip the binding's ``enabled`` flag; ``reason`` is why it was disabled.

        The public ``disabled_reason`` value is the channel-agnostic "this
        credential was switched off by the platform, here is why" record: a
        trigger that hits a permanent upstream failure (revoked token, a
        second poller on the same bot) writes it alongside ``enabled=0`` so
        the panel / status tools can show a readable cause instead of a bare
        inactive toggle. Enabling always clears it — the owner's re-enable
        (or a re-bind) is the fresh start. Goes through ``patch`` so it never
        clobbers a concurrent write to other fields. Enabling a disabled row
        also blanks the descriptor's ``credential_schema.reset_on_reenable``
        fields (see ``patch``).
        """
        try:
            # str() once here so every consumer (panels, tg_status, plugin
            # channels) can rely on the field being a string.
            record = await self.patch(channel, agent_id, {"disabled_reason": str(reason or "") if not enabled else ""}, enabled=enabled)
        except RuntimeError as exc:
            # The version race is the only thing patch raises; keep the old
            # "never raises, False means not done" contract for the route and
            # the triggers so a lost race is a logged failure, not a 500 or a
            # silently-still-enabled dead credential.
            logger.error(f"channel_credentials: {channel}/{agent_id} set_enabled({enabled}) failed: {exc}")
            return False
        return record is not None

    async def list_active(self, channel: str) -> list[CredentialRecord]:
        """Enabled bindings whose secret this install can read — an unreadable one is logged (once per row version) and skipped."""
        rows = await self._db.get(TABLE, {"channel": channel, "enabled": 1})
        out: list[CredentialRecord] = []
        for record in (self._row_to_record(r) for r in rows):
            if record.secret_error:
                key = (record.channel, record.agent_id, record.version)
                if key not in _UNREADABLE_WARNED:
                    _UNREADABLE_WARNED.add(key)
                    logger.warning(f"channel_credentials: {record.channel} binding of agent {record.agent_id} skipped: {record.secret_error}")
                continue
            out.append(record)
        return out

    async def list_for_agent(self, agent_id: str) -> list[CredentialRecord]:
        """Every channel binding of one agent (bundle export, agent deletion)."""
        rows = await self._db.get(TABLE, {"agent_id": agent_id})
        return [self._row_to_record(r) for r in rows]

    async def list_all(self, channel: str) -> list[CredentialRecord]:
        rows = await self._db.get(TABLE, {"channel": channel})
        return [self._row_to_record(r) for r in rows]


__all__ = ["CredentialConflict", "CredentialRecord", "GenericCredentialStore", "TABLE", "UnknownChannel", "all_descriptors", "bind_fields_for", "descriptor_for", "missing_required", "split_values", "validate_bind_fields"]
