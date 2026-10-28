"""
@file_name: registry.py
@author: Bin Liang
@date: 2026-09-04
@description: ``ModuleRegistry`` — the read-only mapping (class name → module class) over the kernel ``agent.capabilities.modules`` registry; ``module_registry`` is the process-wide one.

The platform's ONLY way to name a module (plugin platform batch 5d): there is
no ``MODULE_MAP`` table any more — a builtin and a plugin module are both
just contributions in the registry, a builtin disabled through
registry.json is simply absent, and the package no longer re-exports module
classes. Builds are cached per registry generation so hot paths pay a dict
lookup; ``meta`` / ``owner_of`` expose the contribution's plugin id.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterator


class ModuleRegistry(Mapping[str, type]):
    def __init__(self, registries: Any = None) -> None:
        from narranexus.platform.module_system.slots import MODULES_SLOT

        self._slot = MODULES_SLOT
        self._registries = registries
        self._cache: dict[str, type] | None = None
        self._cache_key: tuple[str, ...] | None = None

    def _registry(self):
        if self._registries is None:
            from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

            return KERNEL_REGISTRIES.registry_for(self._slot)
        return self._registries.registry_for(self._slot)

    def _build(self) -> dict[str, type]:
        registry = self._registry()
        key = registry.names()
        if self._cache is not None and key == self._cache_key:
            return self._cache
        built: dict[str, type] = {}
        for entry in registry.entries():
            try:
                built[entry.name] = entry.factory()
            except Exception as exc:  # noqa: BLE001 — a module whose import fails is absent, not fatal
                from loguru import logger

                logger.warning(f"[modules] {entry.owner}: module {entry.name!r} failed to import: {exc}")
        self._cache, self._cache_key = built, key
        return built

    def meta(self, name: str) -> dict[str, Any]:
        registry = self._registry()
        for entry in registry.entries():
            if entry.name == name:
                return dict(entry.meta)
        raise KeyError(name)

    def owner_of(self, name: str) -> str:
        return self._registry().owner_of(name)

    def __getitem__(self, name: str) -> type:
        return self._build()[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._build())

    def __len__(self) -> int:
        return len(self._build())

    def __contains__(self, name: object) -> bool:
        return name in self._build()

    def __repr__(self) -> str:
        return f"ModuleRegistry({list(self._build())})"




#: The process-wide registry view (KERNEL_REGISTRIES).
module_registry = ModuleRegistry()

__all__ = ["ModuleRegistry", "module_registry"]


def module_class_for(plugin_id: str, registries: Any = None) -> type:
    """The module class a plugin contributes (its ``api.module_class()`` facade delegates here): the
    ``agent.capabilities.modules`` entry whose owner is ``plugin_id``. KeyError names the plugin when
    it contributes no module in this process (not booted, disabled, or not a module plugin)."""
    view = ModuleRegistry(registries)
    for entry in view._registry().entries():
        if entry.owner == plugin_id:
            return entry.factory()
    raise KeyError(f"{plugin_id} contributes no module (agent.capabilities.modules) in this process")


def core_module_names(registries: Any = None) -> list[str]:
    """Module names that are NOT channel modules (channel modules are ChannelModuleBase subclasses; the meta says so)."""
    view = ModuleRegistry(registries)
    return sorted(e.name for e in view._registry().entries() if not e.meta.get("channel"))
