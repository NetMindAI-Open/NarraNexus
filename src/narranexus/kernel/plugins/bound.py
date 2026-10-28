"""
@file_name: bound.py
@author: Bin Liang
@date: 2026-09-07
@description: Reading the RESOLVED bindings at runtime — the seam that turns "which plugin fills this slot" from a snapshot file into the object a host actually uses. ``bound_provider`` answers with the plugin id (binding layer > slot default), ``bound_entry`` / ``bound_entries`` hand back the registry contributions of that provider (one-arity: exactly one; many-arity: filtered and ordered by the binding, else registration order).
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts import UnknownEntry


def _matches(entry: Any, provider: str) -> bool:
    """A binding value names a plugin (owner), a contribution (name) or both (``owner:name``)."""
    if entry.owner == provider or entry.name == provider:
        return True
    owner, sep, name = provider.partition(":")
    return bool(sep) and entry.owner == owner and entry.name == name


def bound_provider(registries: Any, slot: str) -> Optional[str]:
    """Plugin id bound to a one-arity ``slot``: the resolved binding if the host resolved one, else the slot's default."""
    resolved = getattr(registries, "bindings", None)
    if resolved is not None and slot in resolved.one:
        return resolved.one[slot].provider
    spec = registries.slots.get(slot)
    return spec.default


def bound_layer(registries: Any, slot: str) -> str:
    """Name of the layer the one-arity binding came from (``DEFAULT`` when only the slot default applies)."""
    resolved = getattr(registries, "bindings", None)
    if resolved is not None and slot in resolved.one:
        return resolved.one[slot].layer.name
    return "DEFAULT"


def bound_entry(registries: Any, slot: str) -> Any:
    """The single registry entry of the plugin bound to a one-arity slot.

    Matches by OWNER (the binding value is a plugin id). Raises ``UnknownEntry``
    when the bound plugin registered nothing there — a misbinding must be loud,
    never a silent fallback to whichever plugin registered first."""
    provider = bound_provider(registries, slot)
    registry = registries.registry_for(slot)
    if provider is not None:
        for entry in registry.entries():
            if entry.owner == provider:
                return entry
        for entry in registry.entries():  # a binding may also name a contribution directly, or owner:name
            if _matches(entry, provider):
                return entry
    names = sorted({e.owner for e in registry.entries()})
    raise UnknownEntry(f"{slot}: bound provider {provider!r} has no contribution here (registered: {names})")


def bound_entries(registries: Any, slot: str) -> list[Any]:
    """Registry entries of a many-arity slot in binding order.

    With a binding: only the listed providers, in that order (a provider may be
    a plugin id or a contribution name). Without: every entry, registration order."""
    registry = registries.registry_for(slot)
    entries = list(registry.entries())
    resolved = getattr(registries, "bindings", None)
    if resolved is None or slot not in resolved.many or not resolved.many[slot].providers:
        return entries
    wanted = list(resolved.many[slot].providers)
    picked: list[Any] = []
    for provider in wanted:
        picked.extend(e for e in entries if _matches(e, provider) and e not in picked)
    return picked


__all__ = ["bound_entries", "bound_entry", "bound_layer", "bound_provider"]
