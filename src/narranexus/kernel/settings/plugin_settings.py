"""
@file_name: plugin_settings.py
@author: Bin Liang
@date: 2026-09-03
@description: ``PluginSettings`` — typed access to one plugin's settings with env > stored > default precedence.

The kernel owns the precedence rule and the typing; where rows live is a
``SettingsStore`` the host injects (the platform's database store encrypts
secrets with its secret box). Reads are synchronous over a snapshot the store
provides, so a plugin can read settings on a hot path without awaiting; writes
go through the store and refresh the snapshot.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Protocol, runtime_checkable

from narranexus.contracts.settings import SettingsSchema


@runtime_checkable
class SettingsStore(Protocol):
    """Where user-edited values live. Values are already decrypted on the way out."""

    def load(self, plugin_id: str) -> Mapping[str, Any]: ...

    def save(self, plugin_id: str, key: str, value: Any, *, secret: bool) -> None: ...

    def delete(self, plugin_id: str, key: str) -> None: ...


class MemorySettingsStore:
    """In-memory store for tests and the minimal host."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Any] = {}

    def load(self, plugin_id: str) -> Mapping[str, Any]:
        return {k: v for (pid, k), v in self._rows.items() if pid == plugin_id}

    def save(self, plugin_id: str, key: str, value: Any, *, secret: bool) -> None:
        self._rows[(plugin_id, key)] = value

    def delete(self, plugin_id: str, key: str) -> None:
        self._rows.pop((plugin_id, key), None)


class PluginSettings:
    """Typed settings for one plugin.

    Precedence per key: ``NXP_<PLUGIN_ID>_<KEY>`` environment variable, then
    the stored row, then the schema default. A key not in the schema raises
    ``KeyError`` (a plugin cannot read settings it did not declare).
    """

    def __init__(
        self,
        plugin_id: str,
        schema: SettingsSchema,
        *,
        store: SettingsStore | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.schema = schema
        self._store: SettingsStore = store if store is not None else MemorySettingsStore()
        self._environ = environ if environ is not None else os.environ
        self._rows: dict[str, Any] = dict(self._store.load(plugin_id))

    def _field(self, key: str):
        try:
            return self.schema.fields[key]
        except KeyError:
            raise KeyError(f"{self.plugin_id}: setting {key!r} is not declared in the plugin's settings schema") from None

    def source_of(self, key: str) -> str:
        """Which layer answers ``get(key)``: ``env`` | ``stored`` | ``default`` | ``unset``."""
        field = self._field(key)
        if self.schema.env_name(self.plugin_id, key) in self._environ:
            return "env"
        if key in self._rows:
            return "stored"
        return "default" if field.default is not None else "unset"

    def get(self, key: str) -> Any:
        field = self._field(key)
        env_name = self.schema.env_name(self.plugin_id, key)
        if env_name in self._environ:
            return field.coerce(self._environ[env_name])
        if key in self._rows:
            return field.coerce(self._rows[key])
        if field.default is None and field.required:
            raise KeyError(f"{self.plugin_id}: required setting {key!r} is not set")
        return field.default

    def set(self, key: str, value: Any) -> None:
        field = self._field(key)
        coerced = field.coerce(value)
        self._store.save(self.plugin_id, key, coerced, secret=field.secret)
        self._rows[key] = coerced

    def unset(self, key: str) -> None:
        self._field(key)
        self._store.delete(self.plugin_id, key)
        self._rows.pop(key, None)

    def snapshot(self, *, reveal_secrets: bool = False) -> dict[str, Any]:
        """Every declared key with its effective value; secrets masked unless asked."""
        out: dict[str, Any] = {}
        for key, field in self.schema.fields.items():
            try:
                value = self.get(key)
            except KeyError:
                value = None
            if field.secret and value and not reveal_secrets:
                value = "••••••"
            out[key] = value
        return out


__all__ = ["MemorySettingsStore", "PluginSettings", "SettingsStore"]
