"""
@file_name: bindings.py
@author: Bin Liang
@date: 2026-09-03
@description: Resolve which provider fills each slot from six configuration layers.

"Replace an implementation by changing configuration, never code" is the
promise (spec §6.4). A binding names a provider plugin id (optionally
``plugin_id:name`` for a named entry inside that provider) for a slot path.
Layers, highest precedence first:

    TURN           per-turn override (a value object the runtime passes in)
    AGENT          the agent's PipelineProfile (only turn.* / agent.capabilities.*)
    ENV            NX_BIND__TURN__PIPELINE__ACT__FRAMEWORK=builtin.frameworks.claude_code
    USER_CONFIG    ~/.narranexus/narranexus.toml  [bindings] "turn.recall" = "acme.graph_recall"
    DISTRIBUTION   narranexus-dist.json  "bindings": {...}
    DEFAULT        the slot's declared default

``one`` slots take the highest layer that names them. ``many`` slots merge
across layers from lowest to highest with three verbs per entry: ``+id``
appends, ``-id`` removes, ``=a,b`` replaces the whole ordered list.

Everything is resolved once at startup into a ``ResolvedBindings`` snapshot
(written to ``bindings.resolved.json`` so the plugin factory can show "who
fills what, from which layer"). Conflicts are startup errors, never
silent: a distribution-only slot bound from a user layer, a bound slot whose
parent is filled by a non-default provider that does not redeclare it, or a
``one`` slot with neither binding nor default.
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from narranexus.contracts import BindingConflict, UnboundSlot
from narranexus.kernel.plugins.slots import SlotTree, validate_path

ENV_PREFIX = "NX_BIND__"


class Layer(IntEnum):
    """Precedence: a higher number wins for ``one`` slots and merges later for ``many``."""

    DEFAULT = 0
    DISTRIBUTION = 1
    USER_CONFIG = 2
    ENV = 3
    AGENT = 4
    TURN = 5


# Layers a distribution-only slot may be bound from.
_DISTRIBUTION_LAYERS = frozenset({Layer.DEFAULT, Layer.DISTRIBUTION})
# Layers the per-agent / per-turn overlays may touch (spec §6.4 row 1-2).
_AGENT_SCOPED_PREFIXES = ("turn.", "agent.capabilities.")


@dataclass(frozen=True)
class BindingSource:
    """One layer's entries: slot path → provider id (one) or verb list (many)."""

    layer: Layer
    entries: Mapping[str, str | list[str]]
    origin: str = ""  # human-readable: env / toml path / dist id


@dataclass(frozen=True)
class Bound:
    """A resolved ``one`` binding."""

    provider: str
    layer: Layer
    origin: str = ""


@dataclass(frozen=True)
class BoundMany:
    """A resolved ``many`` binding: ordered providers and the layers that shaped it."""

    providers: tuple[str, ...]
    layers: tuple[Layer, ...]


@dataclass
class ResolvedBindings:
    one: dict[str, Bound] = field(default_factory=dict)
    many: dict[str, BoundMany] = field(default_factory=dict)
    # one-arity slots that had neither a binding nor a default (only populated
    # by ``resolve(..., strict=False)``; strict resolution raises instead)
    unbound: list[str] = field(default_factory=list)

    def provider_for(self, path: str) -> str:
        return self.one[path].provider

    def to_json(self) -> dict[str, Any]:
        return {
            "unbound": list(self.unbound),
            "one": {
                p: {"provider": b.provider, "layer": b.layer.name, "origin": b.origin}
                for p, b in sorted(self.one.items())
            },
            "many": {
                p: {"providers": list(b.providers), "layers": [layer.name for layer in b.layers]}
                for p, b in sorted(self.many.items())
            },
        }


# =============================================================================
# Parsing the configuration-backed layers
# =============================================================================


def parse_env(environ: Mapping[str, str] | None = None) -> BindingSource:
    """``NX_BIND__A__B=value`` → ``a.b = value``; many-slot values are comma verbs."""
    env = os.environ if environ is None else environ
    entries: dict[str, str | list[str]] = {}
    for key, raw in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().replace("__", ".")
        validate_path(path)
        entries[path] = _parse_value(raw)
    return BindingSource(Layer.ENV, entries, origin="env")


def parse_toml(text: str, *, origin: str = "narranexus.toml") -> BindingSource:
    """The ``[bindings]`` table of a user config file."""
    data = tomllib.loads(text)
    table = data.get("bindings", {})
    if not isinstance(table, dict):
        raise BindingConflict(f"{origin}: [bindings] must be a table")
    entries: dict[str, str | list[str]] = {}
    for path, raw in table.items():
        validate_path(path)
        entries[path] = _parse_value(raw)
    return BindingSource(Layer.USER_CONFIG, entries, origin=origin)


def from_mapping(layer: Layer, mapping: Mapping[str, Any], *, origin: str) -> BindingSource:
    """A dict of bindings from the distribution manifest or a profile."""
    entries: dict[str, str | list[str]] = {}
    for path, raw in mapping.items():
        validate_path(path)
        entries[path] = _parse_value(raw)
    return BindingSource(layer, entries, origin=origin)


def _parse_value(raw: Any) -> str | list[str]:
    """A plain id for one-slots; a verb list for many-slots.

    A comma anywhere means a list (``a,b`` is shorthand for ``+a,+b``), so a
    user who writes ``NX_BIND__MODEL__PROVIDERS=a,b`` never registers a
    provider literally named ``a,b``.
    """
    if isinstance(raw, list):
        return [str(v).strip() for v in raw]
    text = str(raw).strip()
    if text.startswith(("=", "+", "-")) or "," in text:
        return [t.strip() for t in text.split(",") if t.strip()]
    return text


# =============================================================================
# Resolution
# =============================================================================


def resolve(
    tree: SlotTree,
    sources: Iterable[BindingSource],
    *,
    redeclarations: Mapping[str, Iterable[str]] | None = None,
    strict: bool = True,
) -> ResolvedBindings:
    """Combine the layers over the slot tree.

    ``redeclarations`` maps a provider plugin id to the child slot paths it
    keeps alive when it fills a composite slot (manifest ``redeclares``).

    ``strict=False`` records a one-arity slot with neither binding nor default
    in ``ResolvedBindings.unbound`` instead of raising ``UnboundSlot`` — the
    host uses it so one unbound slot (a plugin declaring a defaultless slot)
    cannot silently void every other binding the operator wrote. Conflicts
    (``BindingConflict``) are loud in both modes.
    """
    redeclared: dict[str, set[str]] = {k: set(v) for k, v in (redeclarations or {}).items()}
    ordered = sorted(sources, key=lambda s: s.layer)
    resolved = ResolvedBindings()

    # A binding for a slot that does not exist is a typo (or a stale example)
    # that would otherwise be dropped silently — the config-driven promise
    # fails without a word. Name the layer and origin so the user can find it.
    unknown = [
        f"{path} (from {src.origin or src.layer.name})"
        for src in ordered
        for path in src.entries
        if path not in tree
    ]
    if unknown:
        raise BindingConflict(f"bindings name slots that do not exist: {', '.join(unknown)}")

    for path in tree.paths():
        slot = tree.get(path)
        if slot.arity == "one":
            try:
                resolved.one[path] = _resolve_one(slot, ordered)
            except UnboundSlot:
                if strict:
                    raise
                resolved.unbound.append(path)
        else:
            resolved.many[path] = _resolve_many(slot, ordered)

    _check_nesting(tree, resolved, redeclared)
    return resolved


def _resolve_one(slot, ordered: list[BindingSource]) -> Bound:
    chosen: Bound | None = None
    if slot.default is not None:
        chosen = Bound(slot.default, Layer.DEFAULT, origin="slot default")
    for src in ordered:  # ascending: later (higher) layers overwrite
        if slot.path not in src.entries:
            continue
        _check_layer_allowed(slot, src)
        value = src.entries[slot.path]
        if isinstance(value, list):
            raise BindingConflict(
                f"{slot.path}: one-arity slot bound with a list from {src.origin or src.layer.name}"
            )
        chosen = Bound(value, src.layer, origin=src.origin or src.layer.name)
    if chosen is None:
        raise UnboundSlot(f"{slot.path}: no binding and no default provider")
    return chosen


def _resolve_many(slot, ordered: list[BindingSource]) -> BoundMany:
    providers: list[str] = []
    layers: list[Layer] = []
    for src in ordered:
        if slot.path not in src.entries:
            continue
        _check_layer_allowed(slot, src)
        value = src.entries[slot.path]
        verbs = value if isinstance(value, list) else [value]
        for verb in verbs:
            if verb.startswith("="):
                # "=" restarts the list. parse_toml / parse_env hand over one
                # verb per provider, but a BindingSource built in code (a
                # distribution's bindings, tests) may carry "=a,b" as one
                # value, so the comma split stays.
                providers = [v for v in verb[1:].split(",") if v]
            elif verb.startswith("-"):
                providers = [p for p in providers if p != verb[1:]]
            else:
                pid = verb[1:] if verb.startswith("+") else verb
                if pid and pid not in providers:
                    providers.append(pid)
        layers.append(src.layer)
    return BoundMany(tuple(providers), tuple(layers))


def _check_layer_allowed(slot, src: BindingSource) -> None:
    if slot.distribution_only and src.layer not in _DISTRIBUTION_LAYERS:
        raise BindingConflict(
            f"{slot.path}: distribution-only slot cannot be bound from the "
            f"{src.layer.name} layer ({src.origin or 'unnamed source'})"
        )
    if src.layer in (Layer.AGENT, Layer.TURN) and not slot.path.startswith(_AGENT_SCOPED_PREFIXES):
        raise BindingConflict(
            f"{slot.path}: the {src.layer.name} layer may only bind turn.* and agent.capabilities.* slots"
        )


def _check_nesting(tree: SlotTree, resolved: ResolvedBindings, redeclared: dict[str, set[str]]) -> None:
    """A non-default parent provider hides the children it does not redeclare."""
    for path, bound in resolved.one.items():
        slot = tree.get(path)
        if bound.provider == slot.default:
            continue
        kept = redeclared.get(bound.provider, set())
        for child in tree.descendants(path):
            child_bound = resolved.one.get(child.path)
            child_many = resolved.many.get(child.path)
            explicitly_bound = (
                child_bound is not None and child_bound.layer is not Layer.DEFAULT
            ) or (child_many is not None and child_many.layers)
            if explicitly_bound and child.path not in kept:
                raise BindingConflict(
                    f"{child.path} is bound, but its parent {path!r} is filled by "
                    f"{bound.provider!r} which does not redeclare it; either bind the parent's "
                    f"default provider, drop the child binding, or have {bound.provider!r} "
                    f"redeclare {child.path!r}"
                )


# =============================================================================
# Snapshot
# =============================================================================


def referenced_plugins(value: Any) -> set[str]:
    """Plugin ids a binding VALUE names, whatever its form.

    The grammar lives here because this module defines it: a one-arity value
    is ``owner``, ``name`` or ``owner:name``; a many-arity value is a list (or
    a comma string) of those, each optionally prefixed with the ``+``/``-``/``=``
    verbs. Every validator of "is this provider in the distribution / a
    candidate" calls this instead of re-deriving the grammar (the third copy
    was the one that rejected ``owner:name``).
    """
    items: list[str]
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        return set()
    out: set[str] = set()
    for item in items:
        for part in item.split(","):
            token = part.strip()
            while token and token[0] in "+-=":
                token = token[1:]
            if not token:
                continue
            out.add(token.split(":", 1)[0])
    return out


def write_resolved(resolved: ResolvedBindings, path: Path) -> Path:
    """Atomically write the resolved snapshot (temp file + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(resolved.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


__all__ = [
    "ENV_PREFIX",
    "Layer",
    "BindingSource",
    "Bound",
    "BoundMany",
    "ResolvedBindings",
    "parse_env",
    "referenced_plugins",
    "parse_toml",
    "from_mapping",
    "resolve",
    "write_resolved",
]
