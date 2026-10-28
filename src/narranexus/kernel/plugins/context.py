"""
@file_name: context.py
@author: Bin Liang
@date: 2026-09-03
@description: ``PluginContext`` — everything a plugin's ``activate(ctx)`` may touch, and nothing else (spec §5.4).

The context is the plugin's whole world: its own settings, a database view
limited to its own ``ext_<id>_`` tables, the event bus, the registries for
the slots its manifest provides, the service locator, a bound logger, and a
``DisposableStack`` that ``deactivate`` unwinds. Values only cross the
boundary (charter 7); the context never hands out the host's app object or
raw registries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping

from loguru import logger

from narranexus.contracts import API_VERSIONS, Disposable, DisposableStack, IncompatibleProvider, PluginError
from narranexus.contracts.table import table_prefix_for
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Registry
from narranexus.kernel.plugins.services import ScopedServices
from narranexus.kernel.settings.plugin_settings import PluginSettings
from narranexus.contracts.distribution import is_builtin_id

Role = Literal["backend", "mcp", "workers"]


class PluginDb:
    """A database client restricted to the plugin's own tables.

    Every method takes the table name first and refuses names outside the
    plugin's ``ext_<id>_`` prefix (builtins may touch core tables). The
    underlying client is the host's ``AsyncDatabaseClient``; only the
    dictionary-style verbs are exposed — raw SQL from plugins is not a
    supported surface in batch 2.
    """

    def __init__(self, plugin_id: str, client: Any) -> None:
        self.plugin_id = plugin_id
        self._client = client
        self._prefix = table_prefix_for(plugin_id)
        self._builtin = is_builtin_id(plugin_id)

    def table(self, name: str) -> str:
        if not self._builtin and not name.startswith(self._prefix):
            raise PluginError(f"{self.plugin_id}: table {name!r} is outside {self._prefix}*")
        return name

    async def get(self, table: str, filters: Mapping[str, Any] | None = None, **kw: Any) -> list[dict[str, Any]]:
        return await self._client.get(self.table(table), dict(filters or {}), **kw)

    async def get_one(self, table: str, filters: Mapping[str, Any]) -> dict[str, Any] | None:
        return await self._client.get_one(self.table(table), dict(filters))

    async def insert(self, table: str, data: Mapping[str, Any]) -> Any:
        return await self._client.insert(self.table(table), dict(data))

    async def update(self, table: str, filters: Mapping[str, Any], data: Mapping[str, Any]) -> Any:
        return await self._client.update(self.table(table), dict(filters), dict(data))

    async def delete(self, table: str, filters: Mapping[str, Any]) -> Any:
        return await self._client.delete(self.table(table), dict(filters))


class ScopedRegistries:
    """Only the slots the manifest provides; anything else is an error, not a silent empty registry."""

    def __init__(self, registries: Registries, plugin_id: str, allowed: tuple[str, ...]) -> None:
        self._registries = registries
        self._plugin_id = plugin_id
        self._allowed = frozenset(allowed)

    def registry_for(self, path: str) -> Registry[Any]:
        if path not in self._allowed:
            raise PluginError(f"{self._plugin_id}: slot {path!r} is not in the manifest's provides {sorted(self._allowed)}")
        return self._registries.registry_for(path)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._allowed))


class ScopedEvents:
    """Subscribe as the plugin; disposals collected on the context stack."""

    def __init__(self, bus: Any, plugin_id: str, stack: DisposableStack) -> None:
        self._bus = bus
        self._plugin_id = plugin_id
        self._stack = stack

    def subscribe(self, name: str, handler: Callable[[Mapping[str, Any]], Awaitable[None] | None]) -> Disposable:
        return self._stack.add(self._bus.subscribe(name, handler, owner=self._plugin_id))

    async def emit(self, name: str, payload: Mapping[str, Any]) -> Any:
        return await self._bus.emit(name, payload)


@dataclass
class PluginContext:
    plugin_id: str
    version: str
    path: Path
    data_dir: Path
    role: Role
    host_version: str
    settings: PluginSettings
    db: PluginDb
    events: ScopedEvents
    registries: ScopedRegistries
    services: ScopedServices
    subscriptions: DisposableStack = field(default_factory=DisposableStack)
    log: Any = None

    def __post_init__(self) -> None:
        if self.log is None:
            self.log = logger.bind(plugin=self.plugin_id)

    def api_version(self, kind: str) -> int:
        try:
            return API_VERSIONS[kind]
        except KeyError:
            raise PluginError(f"unknown contract kind {kind!r}. Known: {sorted(API_VERSIONS)}") from None

    def require_api(self, kind: str, min_version: int) -> None:
        have = self.api_version(kind)
        if have < min_version:
            raise IncompatibleProvider(f"{self.plugin_id}: needs {kind} API >= {min_version}, host has {have}")

    def dispose(self) -> None:
        self.subscriptions.dispose()


def build_context(
    *,
    plugin_id: str,
    version: str,
    path: Path,
    role: Role,
    host_version: str,
    registries: Registries,
    provides: tuple[str, ...],
    settings: PluginSettings,
    db_client: Any,
    bus: Any,
    services: ScopedServices,
    data_dir: Path | None = None,
) -> PluginContext:
    """Assemble a context; hosts call this from the activator with their own db/bus/settings store."""
    stack = DisposableStack()
    ctx = PluginContext(
        plugin_id=plugin_id,
        version=version,
        path=path,
        data_dir=data_dir if data_dir is not None else path / "data",
        role=role,
        host_version=host_version,
        settings=settings,
        db=PluginDb(plugin_id, db_client),
        events=ScopedEvents(bus, plugin_id, stack),
        registries=ScopedRegistries(registries, plugin_id, provides),
        services=services,
        subscriptions=stack,
    )
    return ctx


__all__ = ["PluginContext", "PluginDb", "Role", "ScopedEvents", "ScopedRegistries", "build_context"]
