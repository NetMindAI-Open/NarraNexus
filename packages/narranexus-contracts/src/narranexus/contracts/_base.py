"""
@file_name: _base.py
@author: Bin Liang
@date: 2026-09-03
@description: Primitives every contract shares — Disposable resources, cancellation, error taxonomy.

Everything here is deliberately dependency-free (stdlib only) so that
``narranexus.contracts`` stays a leaf package.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Protocol, runtime_checkable


class Stability(str, Enum):
    """Stability level of an exported contract symbol (docs/API_POLICY.md)."""

    ALPHA = "alpha"
    BETA = "beta"
    STABLE = "stable"


# =============================================================================
# Error taxonomy
# =============================================================================


class PluginError(Exception):
    """Root of every plugin-platform error."""


class RegistryConflict(PluginError):
    """Two providers registered under one name. Raised at startup, never masked."""


class UnknownEntry(PluginError, KeyError):
    """Lookup of a name nobody registered. The platform never falls back silently."""

    def __str__(self) -> str:  # KeyError quotes its argument; keep the message readable
        return Exception.__str__(self)


class RegistryFrozen(PluginError):
    """``register()`` after ``freeze()`` — registration is a startup-time activity."""


class BindingConflict(PluginError):
    """A slot binding contradicts another (e.g. a parent's non-default provider does
    not redeclare a child slot that is also bound, or a distribution-only slot was
    bound from a user layer)."""


class UnboundSlot(PluginError):
    """A ``one``-arity slot has neither a binding nor a default provider."""


class IncompatibleProvider(PluginError):
    """The bound provider's contract version does not satisfy the slot's."""


class ManifestError(PluginError, ValueError):
    """``narranexus-plugin.json`` failed validation."""


# =============================================================================
# Disposable
# =============================================================================


class Disposable:
    """Idempotent release handle returned by every ``register``/``subscribe``.

    Modelled on VS Code's ``Disposable``: the second ``dispose()`` is a no-op, so
    callers can release eagerly without tracking whether they already did.
    """

    __slots__ = ("_fn", "_done")

    def __init__(self, fn: Callable[[], None]) -> None:
        self._fn = fn
        self._done = False

    def dispose(self) -> None:
        if self._done:
            return
        self._done = True
        self._fn()

    @property
    def disposed(self) -> bool:
        return self._done

    def __repr__(self) -> str:
        return f"Disposable(disposed={self._done})"


class DisposableStack:
    """LIFO container of Disposables; ``dispose()`` releases all, aggregating failures.

    Adding to an already-disposed stack disposes the item immediately, so a late
    registration during shutdown cannot leak.
    """

    def __init__(self) -> None:
        self._items: list[Disposable] = []
        self._disposed = False

    def add(self, item: Disposable) -> Disposable:
        if self._disposed:
            item.dispose()
            return item
        self._items.append(item)
        return item

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        errors: list[Exception] = []
        while self._items:
            item = self._items.pop()
            try:
                item.dispose()
            except Exception as exc:  # noqa: BLE001 — aggregate; never lose one release
                errors.append(exc)
        if errors:
            raise ExceptionGroup("dispose failures", errors)

    @property
    def disposed(self) -> bool:
        return self._disposed

    def __len__(self) -> int:
        return len(self._items)


# =============================================================================
# Cancellation
# =============================================================================


@runtime_checkable
class CancellationSignal(Protocol):
    """Structural view of a cancellation token: ``requested() -> bool``.

    Declared here (not imported from the runtime) so contracts stay free of
    platform imports; the platform's real token satisfies it as-is.
    """

    def requested(self) -> bool: ...


def plugin_id_slug(plugin_id: str) -> str:
    """The identifier-safe form of a plugin id: ``.`` and ``-`` become ``_``.

    NOT injective — ``acme.auth-sso`` and ``acme.auth_sso`` (both valid ids)
    share a slug — so every namespace built on it (``ext_<slug>__`` table
    prefixes, ``NXP_<SLUG>__`` env names, ``nxplugins.<slug>`` packages) is
    guarded by a uniqueness check on the slug itself: the installer refuses a
    plugin whose slug an installed plugin or a builtin already holds, and the
    host isolates a plugin whose table prefix another loaded owner holds.
    """
    return plugin_id.replace(".", "_").replace("-", "_")


__all__ = [
    "plugin_id_slug",
    "Stability",
    "PluginError",
    "RegistryConflict",
    "UnknownEntry",
    "RegistryFrozen",
    "BindingConflict",
    "UnboundSlot",
    "IncompatibleProvider",
    "ManifestError",
    "Disposable",
    "DisposableStack",
    "CancellationSignal",
]
