"""
@file_name: trigger.py
@author: Bin Liang
@date: 2026-09-04
@description: Contract for ingress triggers (slot ``ingress.triggers``).

A trigger is a long-lived listener that turns something outside the process
(an IM channel connection, a job clock, an A2A HTTP server) into agent
turns. The plugin registers a ``TriggerSpec`` naming the implementation
class lazily (``"pkg.mod:Class"``) so an optional dependency that is missing
in one deployment isolates that one trigger instead of breaking the import
of the whole map; the host that owns the process resolves and instantiates
it:

- ``host="channels"``  — the channels supervisor: ``cls(max_workers=...)``, then
  ``await pre_start(db)`` / ``await start(db)`` (non-blocking) / ``await stop()``
  (``ChannelTriggerBase`` shape). ``name`` is the channel name.
- ``host="workers"``   — the workers supervisor runs it as a worker:
  ``cls(**kwargs)``, ``await start()`` blocks until ``stop()``.
- ``host="api"``       — an HTTP-serving trigger the module runner exposes on
  demand (``cls(host=, port=, ...)`` then ``run()``); e.g. the A2A server.

Contract version: ``API_VERSIONS["trigger"]``.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

TriggerHost = Literal["channels", "workers", "api"]


@runtime_checkable
class Trigger(Protocol):
    """The minimum every trigger instance offers the host that runs it."""

    async def stop(self) -> None: ...


@dataclass(frozen=True)
class TriggerSpec:
    name: str
    class_ref: str  # "package.module:ClassName", resolved lazily
    host: TriggerHost = "channels"
    kwargs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or ":" in self.name:
            raise ValueError(f"trigger name must be non-empty and contain no ':', got {self.name!r}")
        if self.host not in ("channels", "workers", "api"):
            raise ValueError(f"host must be 'channels', 'workers' or 'api', got {self.host!r}")
        if self.class_ref.count(":") != 1 or not all(self.class_ref.split(":")):
            raise ValueError(f"class_ref must look like 'package.module:ClassName', got {self.class_ref!r}")

    @property
    def class_name(self) -> str:
        return self.class_ref.rsplit(":", 1)[1]

    def resolve(self) -> type:
        """Import and return the trigger class (ImportError / AttributeError surface to the caller)."""
        module_path, cls_name = self.class_ref.rsplit(":", 1)
        return getattr(importlib.import_module(module_path), cls_name)


__all__ = ["Trigger", "TriggerHost", "TriggerSpec"]
