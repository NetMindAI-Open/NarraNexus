"""
@file_name: registries.py
@author: Bin Liang
@date: 2026-09-03
@description: The ``Registries`` facade — one ``Registry`` per slot path, the hook registry, the slot tree.

Platform code takes its registries from here instead of building private
dicts, so "who provides what" is answerable in one place and the loader has a
single object to populate and freeze. The registry for a slot is created on
first request from the slot's own declaration (``Slot.kind`` gives the
contract version, ``Slot.case_insensitive`` the key normalisation) — there is
no path-keyed table beside the tree to keep in step with it.

``KERNEL_REGISTRIES`` is the process-wide instance; tests build their own
``Registries`` to load manifests into a clean slate.
"""
from __future__ import annotations

import threading

from typing import Any

from narranexus.contracts import UnknownEntry
from narranexus.kernel.plugins.hooks import HookRegistry
from narranexus.kernel.plugins.registry import Registry
from narranexus.kernel.plugins.slots import SlotTree, build_kernel_slot_tree

class Registries:
    """All registries of one process, keyed by slot path."""

    def __init__(self, slots: SlotTree | None = None) -> None:
        self._lock = threading.RLock()
        self.slots: SlotTree = slots if slots is not None else build_kernel_slot_tree()
        self.hooks: HookRegistry = HookRegistry()
        # The host hook vocabulary is the kernel's: every process declares the
        # host events (contracts.events) and the fourteen stage hooks
        # (contracts.agent.events) so a plugin's ``backend.hooks`` can target
        # them without the host having to remember to declare each one.
        from narranexus.contracts.agent.events import STAGE_HOOKS
        from narranexus.contracts.events import HOST_EVENTS, host_event_params
        from narranexus.kernel.plugins.hooks import HookSpec

        for name in HOST_EVENTS:
            self.hooks.declare(HookSpec(name, host_event_params(name), doc=f"host event {name}"))
        for name, (params, firstresult) in STAGE_HOOKS.items():
            self.hooks.declare(HookSpec(name, params, firstresult=firstresult, doc=f"stage hook {name}"))
        self._by_path: dict[str, Registry[Any]] = {}
        self._frozen = False
        self._bindings: Any = None  # ResolvedBindings once a host resolved them (bound.py reads it)
        # Named services plugins expose to each other and to the platform
        # (see narranexus.contracts.services); one locator per process, so a
        # builtin's service and a user plugin's activate(ctx) share it.
        from narranexus.kernel.plugins.services import ServiceLocator

        self.services: ServiceLocator = ServiceLocator()

    def registry_for(self, path: str) -> Registry[Any]:
        """The registry backing ``path`` (created on first use; the slot must exist)."""
        reg = self._by_path.get(path)
        if reg is None:
            with self._lock:  # two threads asking first must get ONE registry
                reg = self._by_path.get(path)
                if reg is None:
                    slot = self.slots.try_get(path)
                    if slot is None:
                        hint = "" if self._by_path else " (nothing is registered here: has this process booted the plugin platform? hosts.boot / kernel.plugins.builtins.load_builtins)"
                        raise UnknownEntry(f"unknown slot {path!r}. Known: {list(self.slots.paths()) or '[]'}{hint}")
                    reg = Registry(
                        path,
                        api_version=slot.api_version,
                        normalize=slot.normalize if slot.case_insensitive else None,
                    )
                    if self._frozen:
                        reg.freeze()
                    self._by_path[path] = reg
        return reg

    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_path))

    def freeze(self) -> None:
        """Close every surface a plugin can register into, not only the slot
        registries: hooks and the service locator are the other two things
        ``remove_owner`` withdraws, so leaving them open after boot left "nothing
        registers after boot" true for one third of the platform."""
        self._frozen = True
        for reg in self._by_path.values():
            reg.freeze()
        self.hooks.freeze()
        self.services.freeze()

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def bindings(self) -> Any:
        """The host's resolved slot bindings (``None`` until ``set_bindings``); consumers go through ``kernel.plugins.bound``."""
        return self._bindings

    def set_bindings(self, resolved: Any) -> None:
        self._bindings = resolved

    def remove_owner(self, owner: str) -> int:
        """Remove ``owner``'s contributions from every registry and block its hooks (disabled builtin)."""
        removed = sum(reg.remove_owner(owner) for reg in self._by_path.values())
        removed += self.hooks.block(owner)
        removed += self.services.release_owner(owner)
        return removed

    def snapshot(self) -> dict[str, dict[str, str]]:
        """slot path -> {entry name -> owner}; deterministic, for reports and tests."""
        return {
            path: {e.name: e.owner for e in self._by_path[path].entries()}
            for path in sorted(self._by_path)
        }


KERNEL_REGISTRIES = Registries()

__all__ = ["Registries", "KERNEL_REGISTRIES"]
