"""
@file_name: test_bindings_partial.py
@author: Bin Liang
@date: 2026-09-07
@description: One unbound one-arity slot must not void every other binding: resolve(strict=False) records it in ResolvedBindings.unbound and still installs the rest; strict resolution keeps raising; conflicts stay loud in both modes.
"""
from __future__ import annotations

import pytest

from narranexus.kernel.plugins.bindings import BindingConflict, BindingSource, Layer, UnboundSlot, parse_env, resolve
from narranexus.kernel.plugins.slots import Slot, SlotTree


def _tree() -> SlotTree:
    tree = SlotTree()
    tree.declare(Slot("kernel", arity="many", contract="x:Y", owner="builtin.kernel"), create_namespaces=True)
    tree.declare(Slot("kernel.auth", arity="one", contract="x:Y", owner="builtin.kernel", default="builtin.auth.local"), create_namespaces=True)
    tree.declare(Slot("acme", arity="many", contract="x:Y", owner="acme.p"), create_namespaces=True)
    tree.declare(Slot("acme.defaultless", arity="one", contract="x:Y", owner="acme.p"), create_namespaces=True)
    return tree


def test_non_strict_keeps_the_other_bindings_and_names_the_unbound_slot():
    env = parse_env({"NX_BIND__kernel__auth": "builtin.auth.netmind"})
    resolved = resolve(_tree(), [env], strict=False)
    assert resolved.one["kernel.auth"].provider == "builtin.auth.netmind"
    assert resolved.unbound == ["acme.defaultless"]
    assert "acme.defaultless" not in resolved.one
    assert resolved.to_json()["unbound"] == ["acme.defaultless"]


def test_strict_still_raises():
    with pytest.raises(UnboundSlot, match="acme.defaultless"):
        resolve(_tree(), [], strict=True)


def test_conflicts_are_loud_in_non_strict_mode():
    src = BindingSource(layer=Layer.ENV, entries={"kernel.auth": ["a", "b"]}, origin="env")
    with pytest.raises(BindingConflict):
        resolve(_tree(), [src], strict=False)
