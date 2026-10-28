"""
@file_name: registry.py
@author: Bin Liang
@date: 2026-09-03
@description: ``Registry[T]`` — the one shape every single-provider (``one``-arity) slot uses.

Modelled on ``agent_framework/loop/driver.py``'s framework registry, which was
the codebase's cleanest extension point: a name → lazy factory map, a
fail-loud lookup, deterministic ordering. Generalised here so frameworks,
provider drivers, memory kinds and every future ``one`` slot share exactly one
implementation and one set of tests.

Semantics:
- ``register`` returns a ``Disposable``; disposing before ``freeze`` unregisters.
- A duplicate name raises ``RegistryConflict`` unless ``replace=True`` — the
  carve-out exists for the legacy registries' test fixtures and is recorded per
  call in the entry's ``replaced`` flag so the loader can report it.
- ``get`` never falls back: an unknown name raises ``UnknownEntry``.
- ``names()`` is registration order; the loader feeds manifests in a fixed
  order (``loader.load_order``: builtins as declared, then user plugins by id),
  so anything derived from it (prompt sections, tool lists) is byte-stable
  across restarts.
- After ``freeze()`` registration raises ``RegistryFrozen``; registration is a
  startup activity, never a per-request one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterator, Mapping, TypeVar

from loguru import logger

from narranexus.contracts import Disposable, RegistryConflict, RegistryFrozen, UnknownEntry

T = TypeVar("T")


@dataclass(frozen=True)
class Contribution(Generic[T]):
    """What a plugin hands the loader for one slot: a name and a lazy factory.

    Builtin modules define these as module-level constants and register them
    at import; the loader registers the very same objects from the manifest,
    which the registry treats as an idempotent no-op (same factory object under
    the same name). That is how import-time and manifest-driven registration
    coexist without double entries during the migration.
    """

    name: str
    factory: Callable[[], T]
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Entry(Generic[T]):
    """One registration: who provided what, plus free-form metadata for UIs/docs."""

    name: str
    factory: Callable[[], T]
    owner: str
    meta: Mapping[str, Any] = field(default_factory=dict)
    replaced: bool = False


class Registry(Generic[T]):
    """Name → lazy factory map for a ``one``-arity slot."""

    def __init__(
        self,
        kind: str,
        *,
        api_version: int,
        normalize: Callable[[str], str] | None = None,
    ) -> None:
        self.kind = kind
        self.api_version = api_version
        self._normalize = normalize
        self._entries: dict[str, Entry[T]] = {}
        self._frozen = False

    # ------------------------------------------------------------------ keys

    def _key(self, name: str) -> str:
        return self._normalize(name) if self._normalize else name

    # ------------------------------------------------------------- mutation

    def register(
        self,
        name: str,
        factory: Callable[[], T],
        *,
        owner: str,
        meta: Mapping[str, Any] | None = None,
        replace: bool = False,
    ) -> Disposable:
        """Add a provider. Raises on duplicates (unless ``replace``) and after freeze."""
        key = self._key(name)
        existing = self._entries.get(key)
        if existing is not None and (existing.factory is factory or (existing.owner == owner and not replace)):
            # Idempotent: the same contribution registered twice (import-time
            # and manifest-driven) is one entry — a no-op even after freeze,
            # since nothing changes (a second host boot in one process, tests).
            # The same owner re-registering the same name with a fresh factory
            # object is the same situation after a module re-import (the
            # providing module was purged from sys.modules and re-executed);
            # the first registration stands. A different owner still conflicts.
            if existing.factory is not factory:
                logger.info(f"[registry:{self.kind}] {key!r} re-registered by its owner {owner!r} with a different factory; keeping the first")
            return Disposable(lambda: None)
        if self._frozen:
            raise RegistryFrozen(f"{self.kind}: cannot register {name!r} after freeze()")
        if existing is not None and not replace:
            raise RegistryConflict(
                f"{self.kind}: {key!r} is already provided by {existing.owner!r}; "
                f"{owner!r} tried to register it again"
            )
        if existing is not None:
            logger.debug(f"[registry:{self.kind}] {key!r} replaced: {existing.owner!r} -> {owner!r}")
        entry = Entry(name=key, factory=factory, owner=owner, meta=dict(meta or {}), replaced=existing is not None)
        self._entries[key] = entry

        def _dispose() -> None:
            if self._frozen:
                logger.debug(f"[registry:{self.kind}] dispose of {key!r} after freeze ignored")
                return
            if self._entries.get(key) is entry:
                del self._entries[key]

        return Disposable(_dispose)

    def register_contribution(
        self, contribution: Contribution[T], *, owner: str, replace: bool = False
    ) -> Disposable:
        return self.register(
            contribution.name,
            contribution.factory,
            owner=owner,
            meta=contribution.meta,
            replace=replace,
        )

    def freeze(self) -> None:
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen

    # --------------------------------------------------------------- lookup

    def get(self, name: str) -> T:
        """Build the provider. Unknown names fail loud — never a silent default."""
        key = self._key(name)
        try:
            entry = self._entries[key]
        except KeyError:
            hint = "" if self._entries else " (nothing is registered here: has this process booted the plugin platform? hosts.boot / kernel.plugins.builtins.load_builtins)"
            raise UnknownEntry(
                f"{self.kind}: unknown entry {key!r}. Registered: {list(self._entries) or '[]'}{hint}"
            ) from None
        return entry.factory()

    def try_get(self, name: str) -> T | None:
        entry = self._entries.get(self._key(name))
        return entry.factory() if entry is not None else None

    def owner_of(self, name: str) -> str:
        key = self._key(name)
        try:
            return self._entries[key].owner
        except KeyError:
            raise UnknownEntry(
                f"{self.kind}: unknown entry {key!r}. Registered: {list(self._entries) or '[]'}"
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def remove_owner(self, owner: str) -> int:
        """Drop every entry ``owner`` registered (a disabled builtin at boot). Refused after freeze."""
        if self._frozen:
            raise RegistryFrozen(f"{self.kind}: cannot remove {owner!r} after freeze()")
        victims = [k for k, e in self._entries.items() if e.owner == owner]
        for k in victims:
            del self._entries[k]
        return len(victims)

    def entries(self) -> tuple[Entry[T], ...]:
        return tuple(self._entries.values())

    # ----------------------------------------------------------- protocol

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self._key(name) in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __repr__(self) -> str:
        return f"Registry(kind={self.kind!r}, names={list(self._entries)}, frozen={self._frozen})"


__all__ = ["Contribution", "Entry", "Registry"]
