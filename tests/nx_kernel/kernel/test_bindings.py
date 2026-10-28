"""
@file_name: test_bindings.py
@author: Bin Liang
@date: 2026-09-03
@description: Six-layer binding resolution — precedence, many-merge verbs, env/toml parsing, conflicts.
"""
from __future__ import annotations

import json

import pytest

from narranexus.contracts import BindingConflict, UnboundSlot
from narranexus.kernel.plugins.bindings import (
    BindingSource,
    Layer,
    from_mapping,
    parse_env,
    parse_toml,
    resolve,
    write_resolved,
)
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
from narranexus.kernel.plugins.slots import Slot, SlotTree, build_kernel_slot_tree


def _tree() -> SlotTree:
    t = SlotTree()
    t.declare(Slot("kernel", "one", "x:K", "builtin.kernel", default="builtin.kernel", distribution_only=True))
    t.declare(Slot("kernel.auth", "one", "x:A", "builtin.kernel", default="builtin.auth.local", distribution_only=True))
    t.declare(Slot("turn", "one", "x:T", "builtin.kernel", default="builtin.turn"))
    t.declare(Slot("turn.pipeline", "one", "x:P", "builtin.kernel", default="builtin.turn"))
    t.declare(Slot("turn.pipeline.recall", "one", "x:R", "builtin.turn", default="builtin.turn"))
    t.declare(Slot("turn.pipeline.hooks", "many", "x:H", "builtin.turn"))
    t.declare(Slot("model", "one", "x:N", "builtin.kernel", default="builtin.kernel"))
    t.declare(Slot("model.providers", "many", "x:D", "builtin.kernel"))
    t.declare(Slot("model.resolver", "one", "x:M", "builtin.kernel"))  # no default
    return t


def _src(layer: Layer, **entries) -> BindingSource:
    return BindingSource(layer, {k.replace("__", "."): v for k, v in entries.items()}, origin=layer.name.lower())


def test_defaults_apply_and_a_slot_without_default_or_binding_is_unbound():
    tree = _tree()
    with pytest.raises(UnboundSlot, match="model.resolver"):
        resolve(tree, [])
    resolved = resolve(tree, [_src(Layer.DISTRIBUTION, model__resolver="builtin.providers")])
    assert resolved.provider_for("turn.pipeline.recall") == "builtin.turn"
    assert resolved.one["turn.pipeline.recall"].layer is Layer.DEFAULT
    assert resolved.provider_for("model.resolver") == "builtin.providers"


def test_higher_layer_wins_for_one_slots():
    tree = _tree()
    base = [_src(Layer.DISTRIBUTION, model__resolver="builtin.providers")]
    resolved = resolve(
        tree,
        base
        + [
            _src(Layer.USER_CONFIG, turn__pipeline__recall="user.recall"),
            _src(Layer.ENV, turn__pipeline__recall="env.recall"),
            _src(Layer.AGENT, turn__pipeline__recall="agent.recall"),
            _src(Layer.TURN, turn__pipeline__recall="turn.recall"),
        ],
    )
    b = resolved.one["turn.pipeline.recall"]
    assert (b.provider, b.layer) == ("turn.recall", Layer.TURN)
    resolved = resolve(tree, base + [_src(Layer.USER_CONFIG, turn__pipeline__recall="user.recall"),
                                     _src(Layer.ENV, turn__pipeline__recall="env.recall")])
    assert resolved.provider_for("turn.pipeline.recall") == "env.recall"


def test_many_slots_merge_with_plus_minus_and_replace_verbs():
    tree = _tree()
    resolved = resolve(
        tree,
        [
            _src(Layer.DISTRIBUTION, model__resolver="builtin.providers", model__providers=["=a,b,c"]),
            _src(Layer.USER_CONFIG, model__providers=["-b", "+d"]),
            _src(Layer.ENV, model__providers="+e"),
        ],
    )
    many = resolved.many["model.providers"]
    assert many.providers == ("a", "c", "d", "e")
    assert many.layers == (Layer.DISTRIBUTION, Layer.USER_CONFIG, Layer.ENV)
    assert resolved.many["turn.pipeline.hooks"].providers == ()


def test_distribution_only_slot_rejects_user_and_env_layers():
    tree = _tree()
    base = [_src(Layer.DISTRIBUTION, model__resolver="builtin.providers", kernel__auth="acme.sso")]
    assert resolve(tree, base).provider_for("kernel.auth") == "acme.sso"
    with pytest.raises(BindingConflict, match="distribution-only"):
        resolve(tree, base + [_src(Layer.USER_CONFIG, kernel__auth="me.auth")])
    with pytest.raises(BindingConflict, match="distribution-only"):
        resolve(tree, base + [_src(Layer.ENV, kernel__auth="me.auth")])


def test_agent_and_turn_layers_are_limited_to_agent_scoped_slots():
    tree = _tree()
    base = [_src(Layer.DISTRIBUTION, model__resolver="builtin.providers")]
    with pytest.raises(BindingConflict, match="may only bind turn"):
        resolve(tree, base + [_src(Layer.AGENT, model__resolver="agent.resolver")])


def test_parent_replaced_without_redeclare_conflicts_with_child_binding():
    tree = _tree()
    base = [_src(Layer.DISTRIBUTION, model__resolver="builtin.providers")]
    sources = base + [_src(Layer.USER_CONFIG, turn__pipeline="acme.turn", turn__pipeline__recall="acme.recall")]
    with pytest.raises(BindingConflict, match="does not redeclare it"):
        resolve(tree, sources)
    resolved = resolve(tree, sources, redeclarations={"acme.turn": ["turn.pipeline.recall"]})
    assert resolved.provider_for("turn.pipeline") == "acme.turn"
    assert resolved.provider_for("turn.pipeline.recall") == "acme.recall"
    # Replacing the parent alone is fine: un-bound children keep their defaults silently.
    resolved = resolve(tree, base + [_src(Layer.USER_CONFIG, turn__pipeline="acme.turn")])
    assert resolved.provider_for("turn.pipeline.recall") == "builtin.turn"


def test_many_child_binding_under_replaced_parent_also_conflicts():
    tree = _tree()
    sources = [
        _src(Layer.DISTRIBUTION, model__resolver="builtin.providers"),
        _src(Layer.USER_CONFIG, turn__pipeline="acme.turn", turn__pipeline__hooks="+x"),
    ]
    with pytest.raises(BindingConflict, match="turn.pipeline.hooks is bound"):
        resolve(tree, sources)


def test_one_slot_bound_with_a_list_is_a_conflict():
    tree = _tree()
    with pytest.raises(BindingConflict, match="bound with a list"):
        resolve(tree, [_src(Layer.DISTRIBUTION, model__resolver=["=a"])])


def test_parse_env_and_toml_and_mapping():
    env = parse_env({"NX_BIND__TURN__PIPELINE__RECALL": "acme.recall", "NX_BIND__MODEL__PROVIDERS": "+x,-y", "OTHER": "1"})
    assert env.layer is Layer.ENV
    assert env.entries == {"turn.pipeline.recall": "acme.recall", "model.providers": ["+x", "-y"]}
    assert parse_env({"NX_BIND__MODEL__PROVIDERS": "a,b"}).entries == {"model.providers": ["a", "b"]}
    with pytest.raises(ValueError):
        parse_env({"NX_BIND__Bad-Path": "x"})

    toml = parse_toml('[bindings]\n"turn.pipeline.recall" = "acme.recall"\n"model.providers" = ["=a", "+b"]\n')
    assert toml.layer is Layer.USER_CONFIG
    assert toml.entries == {"turn.pipeline.recall": "acme.recall", "model.providers": ["=a", "+b"]}
    assert parse_toml("").entries == {}
    with pytest.raises(BindingConflict):
        parse_toml("bindings = 3\n")

    dist = from_mapping(Layer.DISTRIBUTION, {"kernel.auth": "acme.sso"}, origin="acme.dist")
    assert dist.origin == "acme.dist" and dist.entries == {"kernel.auth": "acme.sso"}


def test_resolved_snapshot_is_written_atomically_and_names_layers(tmp_path):
    tree = _tree()
    resolved = resolve(tree, [_src(Layer.DISTRIBUTION, model__resolver="builtin.providers"),
                              parse_env({"NX_BIND__TURN__PIPELINE__RECALL": "env.recall"})])
    out = write_resolved(resolved, tmp_path / "run" / "bindings.resolved.json")
    data = json.loads(out.read_text())
    assert data["one"]["turn.pipeline.recall"] == {"provider": "env.recall", "layer": "ENV", "origin": "env"}
    assert data["one"]["turn.pipeline"]["layer"] == "DEFAULT"
    assert "model.providers" in data["many"]
    assert not (tmp_path / "run" / "bindings.resolved.json.tmp").exists()


def test_binding_an_unknown_slot_fails_loud_naming_the_origin():
    tree = _tree()
    with pytest.raises(BindingConflict, match=r"turn.act.framework \(from env\)"):
        resolve(
            tree,
            [_src(Layer.DISTRIBUTION, model__resolver="builtin.providers"),
             parse_env({"NX_BIND__TURN__ACT__FRAMEWORK": "x"})],
        )


def test_nesting_rule_fires_on_the_kernel_tree():
    """Replacing the whole turn runtime hides the framework slot unless redeclared."""
    tree = slot_tree_with_builtins()  # the framework slot is builtin.turn's declaration
    dist = from_mapping(Layer.DISTRIBUTION, {"model.resolver": "builtin.providers"}, origin="dist")
    user = from_mapping(
        Layer.USER_CONFIG,
        {"turn.pipeline": "acme.turn", "turn.pipeline.act.framework": "builtin.frameworks.claude_code"},
        origin="narranexus.toml",
    )
    with pytest.raises(BindingConflict, match="turn.pipeline.act.framework is bound"):
        resolve(tree, [dist, user])
    resolved = resolve(tree, [dist, user], redeclarations={"acme.turn": ["turn.pipeline.act.framework"]})
    assert resolved.provider_for("turn.pipeline.act.framework") == "builtin.frameworks.claude_code"
