"""
@file_name: test_bound.py
@author: Bin Liang
@date: 2026-09-07
@description: Runtime consumption of the resolved bindings: bound_provider falls back to the slot default, a resolved binding overrides it, bound_entry matches by owner (or contribution name) and is loud when the bound plugin registered nothing, bound_entries filters and orders a many-slot by the binding.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import UnknownEntry
from narranexus.kernel.plugins.bindings import BindingSource, Layer, resolve
from narranexus.kernel.plugins.bound import bound_entries, bound_entry, bound_layer, bound_provider
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution


def _regs():
    regs = Registries(slot_tree_with_builtins())
    reg = regs.registry_for("prompt.assembler")
    reg.register_contribution(Contribution("default", lambda: "builtin-assembler"), owner="builtin.prompts")
    reg.register_contribution(Contribution("brand", lambda: "acme-assembler"), owner="acme.brand")
    secs = regs.registry_for("prompt.sections")
    for name in ("security", "narrative", "modules"):
        secs.register_contribution(Contribution(name, lambda n=name: n), owner="builtin.prompts")
    secs.register_contribution(Contribution("persona", lambda: "persona"), owner="acme.brand")
    return regs


def test_default_binding_is_the_slot_default_and_entries_follow_registration_order():
    regs = _regs()
    assert bound_provider(regs, "prompt.assembler") == "builtin.prompts" and bound_layer(regs, "prompt.assembler") == "DEFAULT"
    assert bound_entry(regs, "prompt.assembler").factory() == "builtin-assembler"
    assert [e.name for e in bound_entries(regs, "prompt.sections")] == ["security", "narrative", "modules", "persona"]


def test_resolved_bindings_override_and_order():
    regs = _regs()
    src = BindingSource(Layer.USER_CONFIG, {"prompt.assembler": "acme.brand", "prompt.sections": ["acme.brand", "builtin.prompts:modules", "narrative"]}, origin="narranexus.toml")
    regs.set_bindings(resolve(slot_tree_with_builtins(), [src]))
    assert bound_provider(regs, "prompt.assembler") == "acme.brand" and bound_layer(regs, "prompt.assembler") == "USER_CONFIG"
    assert bound_entry(regs, "prompt.assembler").factory() == "acme-assembler"
    names = [e.name for e in bound_entries(regs, "prompt.sections")]
    assert names == ["persona", "modules", "narrative"]  # owner, owner:name and bare name forms, in binding order
    # a binding written as owner:name for a one-arity slot resolves too (found live: the CLI writes that form)
    regs.set_bindings(resolve(slot_tree_with_builtins(), [BindingSource(Layer.USER_CONFIG, {"prompt.assembler": "acme.brand:brand"}, origin="t")]))
    assert bound_entry(regs, "prompt.assembler").factory() == "acme-assembler"


def test_bound_plugin_without_a_contribution_is_loud():
    regs = _regs()
    src = BindingSource(Layer.USER_CONFIG, {"prompt.assembler": "acme.ghost"}, origin="narranexus.toml")
    regs.set_bindings(resolve(slot_tree_with_builtins(), [src]))
    with pytest.raises(UnknownEntry, match="acme.ghost"):
        bound_entry(regs, "prompt.assembler")
