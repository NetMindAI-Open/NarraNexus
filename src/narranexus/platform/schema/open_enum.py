"""
@file_name: open_enum.py
@author: Bin Liang
@date: 2026-09-04
@description: OpenStrEnum — an Enum-shaped str class whose members can be registered after import (plugin platform batch 4).

``WorkingSource`` and ``TriggerType`` label a turn by the surface that
started it. Their core members are fixed, but an IM channel — builtin or
plugin — must be able to add its own value without a platform release, so
the platform itself holds no table of channel names: a channel's descriptor
registers its value at import. The Enum surface is kept (``.value`` /
``.name``, ``Cls("job")``, iteration, membership, pydantic and JSON — it is
a ``str``). Unknown attribute access (``Cls.ACME_CHAT`` before the plugin
registered it) raises a clear ``AttributeError`` at runtime while the
metaclass ``__getattr__`` keeps static checkers from flagging registered
channel members.
"""
from __future__ import annotations

from typing import Any, ClassVar


class _OpenEnumMeta(type):
    """Enum-like class behaviour: iteration, ``in``, ``__members__``, tolerant attribute typing."""

    def __iter__(cls):
        return iter(cls._members.values())  # type: ignore[attr-defined]

    def __len__(cls) -> int:
        return len(cls._members)  # type: ignore[attr-defined]

    def __contains__(cls, item: object) -> bool:
        return isinstance(item, str) and str(item).lower() in cls._members  # type: ignore[attr-defined]

    @property
    def __members__(cls):
        return dict(cls._members)  # type: ignore[attr-defined]

    def __getattr__(cls, name: str) -> Any:
        # Only reached when the attribute is missing: a channel member that was
        # never registered (or is read before its descriptor was imported).
        if name.startswith("_"):
            raise AttributeError(name)
        raise AttributeError(
            f"{cls.__name__} has no member {name!r} — an IM channel registers its value "
            f"from its ChannelDescriptor (``{cls.__name__}.register(<name>)``)"
        )


class OpenStrEnum(str, metaclass=_OpenEnumMeta):
    """Base for the open, string-valued enums. Subclasses get their own member tables."""

    _members: ClassVar[dict[str, Any]]
    _channel_values: ClassVar[set[str]]
    _name_: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._members = {}
        cls._channel_values = set()

    def __new__(cls, value: object):
        key = str(value).lower()
        member = cls._members.get(key)
        if member is None:
            valid = list(cls._members)
            raise ValueError(f"Invalid {cls.__name__}: {value!r}. Valid values: {valid}")
        return member

    @classmethod
    def _add(cls, name: str, value: str, *, channel: bool = False):
        obj = str.__new__(cls, value)
        obj._name_ = name
        cls._members[value] = obj
        setattr(cls, name, obj)
        if channel:
            cls._channel_values.add(value)
        return obj

    @classmethod
    def register(cls, value: str, *, name: str | None = None):
        """Register an IM channel's value (idempotent; core names cannot be redefined)."""
        key = str(value).lower()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"{cls.__name__} value must be [a-z0-9_], got {value!r}")
        existing = cls._members.get(key)
        if existing is not None:
            return existing
        return cls._add(name or key.upper(), key, channel=True)

    @classmethod
    def is_channel(cls, value: object) -> bool:
        return str(value).lower() in cls._channel_values

    @classmethod
    def channel_values(cls) -> tuple[str, ...]:
        return tuple(v for v in cls._members if v in cls._channel_values)

    @property
    def value(self) -> str:
        return str.__str__(self)

    @property
    def name(self) -> str:
        return self._name_

    def __repr__(self) -> str:
        return f"<{type(self).__name__}.{self._name_}: {str.__str__(self)!r}>"

    def __reduce__(self):
        return (type(self), (str.__str__(self),))

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any):
        from pydantic_core import core_schema

        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(lambda v: str.__str__(v), when_used="json"),
        )


__all__ = ["OpenStrEnum"]
