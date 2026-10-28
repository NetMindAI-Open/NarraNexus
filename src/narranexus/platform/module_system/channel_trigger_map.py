"""
@file_name: channel_trigger_map.py
@author: NetMind.AI
@date: 2026-07-08
@description: channel name -> IM trigger class, read from the ``ingress.triggers`` registry.

Why this file exists
====================
Every IM channel trigger is a ``ChannelTriggerBase`` subclass living under
``module/<channel>_module/<channel>_trigger.py``. The consolidated supervisor
(``run_channel_triggers``) needs the full set up-front to instantiate and
``start()`` each in ONE process/event-loop.

Since batch 3c.3 the set is not a hand-written table: each channel builtin
plugin provides a ``TriggerSpec`` (``host="channels"``) into ``ingress.triggers``
(``module/contributions.py`` holds the specs; the builtin manifests name them),
and ``CHANNEL_TRIGGER_MAP`` is a live read-only view over that registry. A
channel disabled through registry.json ``builtin_overrides`` is removed from
the registry at boot and therefore never started — no code path lists channels.

This map lives in the ``module`` layer (NOT ``channel``) on purpose: the trigger
subclasses live here, and ``channel`` is a LOWER layer — importing the subclasses
from ``channel`` would invert the dependency direction and re-enter the circular
import that ``channel_trigger_base`` already documents (module -> channel ->
runtime -> module).

Defensive import (per-channel isolation)
========================================
``TriggerSpec.class_ref`` is resolved lazily, one spec at a time. A channel whose
optional dependency is missing (e.g. ``matrix-nio`` for the NarraMessenger
Matrix adapter) is logged and skipped — the supervisor comes up with the
channels that DID load — instead of one ImportError taking down ALL channels.

``registered_trigger_class_names()`` is the registration INTENT (independent of
which optional deps happen to be installed in this env); the guard test
(``tests/channel/test_trigger_startup_alignment.py``) checks it against the
``ChannelTriggerBase`` subclasses discovered on disk, so a channel shipped
without a TriggerSpec still fails CI even if its dep is absent locally.

The key is DERIVED from each class's own ``channel_name`` so the map key and the
class attribute can never drift; the spec name must agree with it.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any, Iterator

from loguru import logger

from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.module_system.slots import TRIGGERS_SLOT


def registered_trigger_class_names(registries: Any = None) -> frozenset[str]:
    """Class names of every channel trigger registered in this process (``host="channels"`` entries of
    ``ingress.triggers``, read from the contribution meta without importing the class) — the registration
    INTENT, independent of which trigger modules imported cleanly."""
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    regs = registries or KERNEL_REGISTRIES
    out = set()
    for entry in regs.registry_for(TRIGGERS_SLOT).entries():
        if entry.meta.get("host", "channels") == "channels" and entry.meta.get("class_ref"):
            out.add(str(entry.meta["class_ref"]).rsplit(":", 1)[1])
    return frozenset(out)


class TriggerMapView(MutableMapping[str, type[ChannelTriggerBase]]):
    """name -> trigger class for ``host="channels"`` specs currently in the registry.

    Rebuilt on every access (cheap: a handful of entries) so a builtin disabled
    at boot disappears without anyone invalidating a cache; failed imports are
    warned about once per access and skipped.

    Item assignment / deletion is an explicit *override layer* on top of the
    registry (``monkeypatch.setitem(CHANNEL_TRIGGER_MAP, "lark", fake)`` /
    ``monkeypatch.delitem(...)`` in tests, nothing in production): an override
    shadows the registry entry of that name, deleting a registry name hides it
    until something is assigned to it again. Registrations themselves only
    ever happen through ``ingress.triggers``.
    """

    def __init__(self, registries: Any = None) -> None:
        self._registries = registries
        self._overrides: dict[str, type[ChannelTriggerBase]] = {}
        self._hidden: set[str] = set()  # registry names deleted through the override layer
        self._cache: dict[str, type[ChannelTriggerBase]] | None = None
        self._cache_key: tuple = ()
        self._warned: set[tuple[str, str]] = set()  # (name, error) already logged

    def _registry(self):
        regs = self._registries
        if regs is None:
            from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

            regs = KERNEL_REGISTRIES
        return regs.registry_for(TRIGGERS_SLOT)

    def _build(self) -> dict[str, type[ChannelTriggerBase]]:
        # Cached on the registry's name tuple (see ModuleRegistry / _ChannelSpecs):
        # every items()/len()/in call used to re-resolve every trigger spec and
        # re-warn about every unavailable one.
        registry = self._registry()
        key = registry.names()
        if self._cache is not None and key == self._cache_key:
            loaded = dict(self._cache)
        else:
            loaded = self._resolve_all(registry)
            self._cache, self._cache_key = dict(loaded), key
        for name in self._hidden:
            loaded.pop(name, None)
        loaded.update(self._overrides)
        return loaded

    def _resolve_all(self, registry: Any) -> dict[str, type[ChannelTriggerBase]]:
        loaded: dict[str, type[ChannelTriggerBase]] = {}
        for entry in registry.entries():
            try:
                spec = entry.factory()
                if spec.host != "channels":
                    continue
                cls = spec.resolve()
            except Exception as e:  # noqa: BLE001 — missing dep / import error in one channel
                signature = (entry.name, f"{type(e).__name__}: {e}")
                if signature not in self._warned:
                    self._warned.add(signature)
                    logger.warning(f"channel trigger {entry.name!r} unavailable, skipped ({type(e).__name__}: {e})")
                continue
            if cls.channel_name != spec.name:
                logger.error(f"channel trigger {entry.name!r}: spec name != class channel_name {cls.channel_name!r}; skipped")
                continue
            # Resolving a trigger class is a READ; the channel's WorkingSource is
            # registered once at contribution time (channel/contributions.py).
            loaded[cls.channel_name] = cls
        return loaded

    def __setitem__(self, name: str, cls: Any) -> None:
        self._hidden.discard(name)
        self._overrides[name] = cls

    def __delitem__(self, name: str) -> None:
        """dict semantics: afterwards ``name not in view`` — the override (if any) goes and the registry name is hidden."""
        if name not in self._build():
            raise KeyError(name)
        self._overrides.pop(name, None)
        self._hidden.add(name)

    def __getitem__(self, name: str) -> type[ChannelTriggerBase]:
        return self._build()[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._build())

    def __len__(self) -> int:
        return len(self._build())

    def __contains__(self, name: object) -> bool:
        return name in self._build()

    def __repr__(self) -> str:
        return f"TriggerMapView({sorted(self._build())})"


# name -> class. Only the channels that are registered AND imported successfully in this env.
CHANNEL_TRIGGER_MAP: Mapping[str, type[ChannelTriggerBase]] = TriggerMapView()

__all__ = ["CHANNEL_TRIGGER_MAP", "TriggerMapView", "registered_trigger_class_names"]
