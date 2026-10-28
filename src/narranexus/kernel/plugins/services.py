"""
@file_name: services.py
@author: Bin Liang
@date: 2026-09-03
@description: ``ServiceRef`` dependency injection: root-scope host services and plugin-scope facades (Backstage style).

Cross-plugin calls never ``import nxplugins.<other>``; a plugin ``expose``s a
facade under a ``ServiceRef`` and a dependant ``require``s it. Root scope
holds the host's own services (secret store, event sink, ...); a plugin
scope sees root plus what plugins exposed. ``require`` of an unknown ref
fails loud with the list of what exists, because a silent ``None`` here is
the "plugin installed but does nothing" failure the platform refuses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from narranexus.contracts import Disposable, PluginError, RegistryConflict, UnknownEntry
# ``ServiceRef`` is contract data (a name plus a type), so it lives in
# ``narranexus.contracts.services`` next to the refs the platform and the
# builtins share; the LOCATOR is kernel machinery and stays here.
from narranexus.contracts.services import ServiceRef

T = TypeVar("T")


@dataclass(frozen=True)
class _Exposed:
    impl: Any
    owner: str


class ServiceLocator:
    """Root-scope registry of services; ``scoped(plugin_id)`` gives a plugin its view."""

    def __init__(self) -> None:
        self._services: dict[str, _Exposed] = {}
        self._frozen = False

    def expose(self, ref: ServiceRef[T], impl: T, *, owner: str, replace: bool = False) -> Disposable:
        if self._frozen:
            raise PluginError(f"service {ref.id!r}: locator is frozen")
        existing = self._services.get(ref.id)
        if existing is not None and not replace:
            raise RegistryConflict(f"service {ref.id!r} is already exposed by {existing.owner!r}")
        entry = _Exposed(impl, owner)
        self._services[ref.id] = entry

        def _dispose() -> None:
            if self._services.get(ref.id) is entry:
                del self._services[ref.id]

        return Disposable(_dispose)

    def require(self, ref: ServiceRef[T]) -> T:
        try:
            return self._services[ref.id].impl
        except KeyError:
            raise UnknownEntry(f"service {ref.id!r} is not exposed. Available: {sorted(self._services) or '[]'}") from None

    def try_require(self, ref: ServiceRef[T]) -> T | None:
        entry = self._services.get(ref.id)
        return entry.impl if entry else None

    def owner_of(self, ref: ServiceRef[Any]) -> str:
        return self._services[ref.id].owner

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._services))

    def release_owner(self, owner: str) -> int:
        """Drop every service ``owner`` exposed (plugin deactivation)."""
        victims = [k for k, v in self._services.items() if v.owner == owner]
        for k in victims:
            del self._services[k]
        return len(victims)

    def freeze(self) -> None:
        self._frozen = True

    def scoped(self, plugin_id: str) -> "ScopedServices":
        return ScopedServices(self, plugin_id)


class ScopedServices:
    """A plugin's view: ``require`` anything, ``expose`` only as itself."""

    def __init__(self, root: ServiceLocator, plugin_id: str) -> None:
        self._root = root
        self.plugin_id = plugin_id

    def require(self, ref: ServiceRef[T]) -> T:
        return self._root.require(ref)

    def try_require(self, ref: ServiceRef[T]) -> T | None:
        return self._root.try_require(ref)

    def expose(self, ref: ServiceRef[T], impl: T) -> Disposable:
        return self._root.expose(ref, impl, owner=self.plugin_id)


# Refs the kernel itself exposes at boot (implementations are the host's).
SECRET_STORE = ServiceRef[Any]("kernel.secret_store")
EVENT_SINK = ServiceRef[Any]("kernel.event_sink")
HOST_VERSION = ServiceRef[str]("kernel.host_version")

__all__ = ["EVENT_SINK", "HOST_VERSION", "SECRET_STORE", "ScopedServices", "ServiceLocator", "ServiceRef"]
