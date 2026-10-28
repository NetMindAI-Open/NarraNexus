"""
@file_name: settings.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for plugin settings schemas (slot ``backend.settings``).

A plugin declares its settings as a small JSON-Schema-like table: typed
fields with defaults, secrets flagged so the host stores them encrypted and
never echoes them back, and enums for the UI to render as selects. The host
resolves a value as ``NXP_<PLUGIN_ID>_<KEY>`` env > stored row > default.

Contract version: ``API_VERSIONS["settings"]``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from narranexus.contracts._base import plugin_id_slug
from typing import Any, Literal, Mapping

FieldType = Literal["string", "integer", "number", "boolean", "enum"]

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class SettingField:
    type: FieldType
    default: Any = None
    secret: bool = False
    description: str = ""
    choices: tuple[str, ...] = ()
    required: bool = False

    def __post_init__(self) -> None:
        if self.type not in ("string", "integer", "number", "boolean", "enum"):
            raise ValueError(f"unsupported setting type {self.type!r}")
        if self.type == "enum" and not self.choices:
            raise ValueError("enum settings need at least one choice")
        if self.secret and self.type != "string":
            raise ValueError("only string settings can be secret")
        if self.default is not None:
            self.coerce(self.default)

    def coerce(self, raw: Any) -> Any:
        """Validate and convert a raw (env/JSON) value to this field's type."""
        if self.type == "string":
            if not isinstance(raw, str):
                raise ValueError(f"expected a string, got {type(raw).__name__}")
            return raw
        if self.type == "boolean":
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.lower() in ("1", "true", "yes", "on"):
                return True
            if isinstance(raw, str) and raw.lower() in ("0", "false", "no", "off", ""):
                return False
            raise ValueError(f"expected a boolean, got {raw!r}")
        if self.type == "integer":
            if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                raise ValueError(f"expected an integer, got {raw!r}")
            return int(raw)
        if self.type == "number":
            if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
                raise ValueError(f"expected a number, got {raw!r}")
            return float(raw)
        if raw not in self.choices:
            raise ValueError(f"expected one of {self.choices}, got {raw!r}")
        return raw


@dataclass(frozen=True)
class SettingsSchema:
    fields: Mapping[str, SettingField] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in self.fields:
            if not _KEY_RE.match(key):
                raise ValueError(f"setting key {key!r} must match {_KEY_RE.pattern}")

    def env_name(self, plugin_id: str, key: str) -> str:
        # ``NXP_<SLUG>__<KEY>``: the double underscore separates the plugin
        # slug from the key so ``a.b`` + ``c_d`` and ``a.b_c`` + ``d`` cannot
        # collide (ids never contain ``__``); two ids with one slug are refused
        # at install (see ``plugin_id_slug``).
        return f"NXP_{plugin_id_slug(plugin_id).upper()}__{key.upper()}"


__all__ = ["FieldType", "SettingField", "SettingsSchema"]
