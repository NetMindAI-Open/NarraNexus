"""
@file_name: test_slots.py
@author: Bin Liang
@date: 2026-09-03
@description: Slot tree — declaration rules, navigation, kernel seeds, semver helpers.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import API_VERSIONS, RegistryConflict, Stability, UnknownEntry
from narranexus.kernel.plugins.compat import Range, Version
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
from narranexus.kernel.plugins.slots import Slot, SlotTree, build_kernel_slot_tree, validate_path


def test_slot_path_grammar():
    assert validate_path("turn.act.framework") == "turn.act.framework"
    for bad in ("Turn", "turn..act", "turn.", "9x", "turn-act", ""):
        with pytest.raises(ValueError):
            validate_path(bad)


def test_many_slot_cannot_carry_a_default_and_arity_is_validated():
    with pytest.raises(ValueError, match="no single default"):
        Slot("a.b", "many", "x:Y", "p", default="q")
    with pytest.raises(ValueError, match="arity"):
        Slot("a.b", "either", "x:Y", "p")  # type: ignore[arg-type]


def test_declare_requires_parent_and_rejects_duplicates():
    tree = SlotTree()
    with pytest.raises(UnknownEntry, match="parent 'turn' is not declared"):
        tree.declare(Slot("turn.recall", "one", "x:Y", "p"))
    tree.declare(Slot("turn", "one", "x:Y", "p", default="p"))
    tree.declare(Slot("turn.recall", "one", "x:Y", "p", default="p"))
    with pytest.raises(RegistryConflict, match="already declared by 'p'"):
        tree.declare(Slot("turn.recall", "one", "x:Y", "q", default="q"))


def test_create_namespaces_fills_missing_ancestors_owned_by_the_declarer():
    tree = SlotTree()
    tree.declare(Slot("acme.weather.sources", "many", "x:Y", "acme.weather"), create_namespaces=True)
    assert tree.paths() == ("acme", "acme.weather", "acme.weather.sources")
    ns = tree.get("acme.weather")
    assert (ns.arity, ns.owner, ns.default) == ("one", "acme.weather", "acme.weather")


def test_navigation_children_descendants_paths():
    tree = SlotTree()
    tree.declare(Slot("turn", "one", "x:Y", "p", default="p"))
    tree.declare(Slot("turn.act", "one", "x:Y", "p", default="p"))
    tree.declare(Slot("turn.act.framework", "one", "x:Y", "p", default="p"))
    tree.declare(Slot("turn.recall", "one", "x:Y", "p", default="p"))
    assert [s.path for s in tree.children("turn")] == ["turn.act", "turn.recall"]
    assert [s.path for s in tree.descendants("turn")] == ["turn.act", "turn.act.framework", "turn.recall"]
    assert tree.paths() == ("turn", "turn.act", "turn.act.framework", "turn.recall")
    assert tree.get("turn.act").parent == "turn" and tree.get("turn").parent is None
    assert "turn.act" in tree and len(tree) == 4 and list(tree)[0] == "turn"
    with pytest.raises(UnknownEntry, match="unknown slot 'nope'"):
        tree.get("nope")
    assert tree.try_get("nope") is None


def test_kernel_seed_tree_has_the_roots_and_is_docs_friendly():
    tree = build_kernel_slot_tree()
    for path in ("kernel.db", "kernel.auth", "turn.pipeline", "model.providers", "model.clients",
                 "agent.capabilities.memory_kinds", "prompt", "ui"):
        assert path in tree
    assert tree.get("kernel.auth").distribution_only is True
    assert tree.get("model.providers").arity == "many"
    # The kernel declares only what it is the authority for: a builtin's children
    # (stage slots, prompt.*, ui.*) arrive with that builtin's manifest.
    assert "turn.pipeline.act.framework" not in tree and "prompt.sections" not in tree and "ui.themes" not in tree
    # Domain roots in display order, titled by their doc (the catalog and docs read both).
    assert [r.path for r in tree.roots()] == ["kernel", "prompt", "turn", "model", "agent", "ingress", "backend", "content", "ui"]
    assert all(r.doc for r in tree.roots())
    rows = tree.to_rows()
    assert rows == sorted(rows, key=lambda r: r["path"])
    assert all(r["stability"] in {Stability.ALPHA.value, Stability.BETA.value, Stability.STABLE.value} for r in rows)
    assert {r["kind"] for r in rows} > {None, "provider", "llm_client", "auth", "module"}


def test_builtins_own_their_sub_slots():
    tree = slot_tree_with_builtins()
    fw = tree.get("turn.pipeline.act.framework")
    assert (fw.owner, fw.default, fw.parent, fw.kind, fw.case_insensitive) == (
        "builtin.turn", "builtin.frameworks.nexus_power", "turn.pipeline.act", "framework", True)
    assert {s.path for s in tree.descendants("turn.pipeline") if s.owner == "builtin.turn"} >= {
        "turn.pipeline.ingress", "turn.pipeline.recall", "turn.pipeline.compose", "turn.pipeline.assemble",
        "turn.pipeline.act", "turn.pipeline.act.framework", "turn.pipeline.commit", "turn.pipeline.reflect",
    }
    assert tree.get("turn.pipeline.act").arity == "many"  # a real declaration, not an auto-namespace
    assert tree.get("turn.profiles").owner == "builtin.turn" and tree.get("turn.profiles").kind == "pipeline_profile"
    assert tree.get("prompt.sections").owner == "builtin.prompts" and tree.get("prompt.assembler").default == "builtin.prompts"
    assert tree.get("ui.themes").owner == "builtin.ui" and tree.get("ui.pages").kind == "ui"


def test_slot_kind_drives_api_version_and_normalisation(monkeypatch):
    # Every kind but "ui" is at version 0 today, so comparing api_version to 0
    # proves nothing: patch a kind to a distinctive number and follow it all the
    # way into the Registry the slot's path creates. Deleting the kind -> version
    # plumbing (Slot.api_version, or Registries handing it to Registry) turns
    # the 3 into a 0.
    monkeypatch.setitem(API_VERSIONS, "framework", 3)
    s = Slot("acme.x", "many", "x:Y", "acme", kind="framework", case_insensitive=True)
    assert s.api_version == 3 and s.normalize("  Claude_Code ") == "claude_code"
    registries = Registries()
    registries.slots.declare(s, create_namespaces=True)
    assert registries.registry_for("acme.x").api_version == 3
    plain = Slot("acme.y", "many", "x:Y", "acme")
    assert plain.api_version == 0 and plain.normalize("Keep") == "Keep"
    registries.slots.declare(plain, create_namespaces=True)
    assert registries.registry_for("acme.y").api_version == 0
    with pytest.raises(ValueError, match="unknown contract kind"):
        Slot("acme.z", "many", "x:Y", "acme", kind="not_a_kind")
    tree = slot_tree_with_builtins()
    assert {s.path for s in tree.by_kind("stage_strategy")} == {f"turn.pipeline.{st}" for st in ("ingress", "recall", "compose", "assemble", "act", "commit", "reflect")}
    assert [s.path for s in tree.by_kind("prompt")] == ["prompt.assembler", "prompt.sections"]


def test_declare_all_is_shallowest_first_so_a_deep_declaration_never_preempts_a_real_one():
    tree = build_kernel_slot_tree()
    deep = Slot("turn.pipeline.act.framework.acme.seat", "many", "x:Seat", "acme.fw")
    real_act = Slot("turn.pipeline.act", "many", "x:Act", "builtin.turn")
    real_fw = Slot("turn.pipeline.act.framework", "one", "x:Fw", "builtin.turn", default="acme.fw")
    assert tree.declare_all([deep, real_fw, real_act]) == 3  # the auto-created acme namespace is not counted
    assert tree.get("turn.pipeline.act").arity == "many" and tree.get("turn.pipeline.act").owner == "builtin.turn"
    assert tree.get("turn.pipeline.act.framework").default == "acme.fw"
    assert tree.get("turn.pipeline.act.framework.acme").owner == "acme.fw"  # auto-namespace, owned by the declarer
    assert tree.declare_all([deep]) == 0  # idempotent


def test_every_slot_contract_symbol_resolves():
    import importlib

    for row in slot_tree_with_builtins().to_rows():
        module_path, _, symbol = row["contract"].partition(":")
        module = importlib.import_module(module_path)
        assert hasattr(module, symbol), f"{row['path']}: {row['contract']} does not exist"


def test_version_parse_order_and_str():
    assert Version.parse("1.2.3") < Version.parse("1.10.0")
    assert Version.parse("1.2") == Version.parse("1.2.0")
    assert Version.parse("1.2.0-beta.1") < Version.parse("1.2.0")
    assert Version.parse("1.2.0-alpha") < Version.parse("1.2.0-beta")
    assert Version.parse("1.2.0+build.7") == Version.parse("1.2.0")
    assert str(Version.parse("1.2.0-rc.2")) == "1.2.0-rc.2"
    with pytest.raises(ValueError):
        Version.parse("v1.2.3")


@pytest.mark.parametrize(
    ("range_text", "inside", "outside"),
    [
        ("*", ["0.0.1", "9.9.9"], []),
        ("1.2.3", ["1.2.3"], ["1.2.4"]),
        (">=1.19 <2", ["1.19.0", "1.99.7"], ["1.18.9", "2.0.0"]),
        ("^1.2.3", ["1.2.3", "1.9.0"], ["1.2.2", "2.0.0"]),
        ("^0.2.1", ["0.2.1", "0.2.9"], ["0.3.0"]),
        ("^0.0.3", ["0.0.3"], ["0.0.4"]),
        ("~1.2.3", ["1.2.3", "1.2.9"], ["1.3.0"]),
    ],
)
def test_range_contains(range_text, inside, outside):
    r = Range.parse(range_text)
    assert all(r.contains(v) for v in inside), range_text
    assert not any(r.contains(v) for v in outside), range_text
    assert str(r) == ("*" if range_text == "*" else range_text.strip())  # both branches round-trip


def test_range_rejects_garbage():
    with pytest.raises(ValueError):
        Range.parse(">= banana")
