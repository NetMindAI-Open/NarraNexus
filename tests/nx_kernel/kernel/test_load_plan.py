"""
@file_name: test_load_plan.py
@author: Bin Liang
@date: 2026-09-03
@description: Dependency topology: order follows dependencies, missing/mismatched deps block the dependant, cycles block every member, blocking is transitive.
"""
from __future__ import annotations

from narranexus.kernel.plugins.loader import plan_load
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.registries import Registries


def _m(tree, pid, version="1.0.0", deps=None, after=None):
    return parse_manifest(
        {"id": pid, "version": version, "displayName": pid, "dependencies": deps or {}, "afterDependencies": after or []},
        tree=tree,
    )


def test_order_follows_dependencies_then_id():
    tree = Registries().slots
    a = _m(tree, "acme.a", deps={"acme.b": ">=1.0"})
    b = _m(tree, "acme.b", after=["acme.c"])
    c = _m(tree, "acme.c")
    z = _m(tree, "acme.z")
    plan = plan_load([z, a, b, c])
    assert [m.id for m in plan.ordered] == ["acme.c", "acme.b", "acme.a", "acme.z"]
    assert plan.blocked == {}


def test_missing_and_mismatched_dependencies_block_the_dependant():
    tree = Registries().slots
    a = _m(tree, "acme.a", deps={"acme.gone": ">=1.0"})
    b = _m(tree, "acme.b", deps={"acme.c": ">=2.0"})
    c = _m(tree, "acme.c", version="1.5.0")
    plan = plan_load([a, b, c])
    assert [m.id for m in plan.ordered] == ["acme.c"]
    assert plan.blocked["acme.a"].startswith("deps_missing: acme.gone")
    assert "does not satisfy >=2.0" in plan.blocked["acme.b"]


def test_cycle_blocks_every_member_and_blocking_is_transitive():
    tree = Registries().slots
    a = _m(tree, "acme.a", deps={"acme.b": ">=1.0"})
    b = _m(tree, "acme.b", deps={"acme.a": ">=1.0"})
    d = _m(tree, "acme.d", deps={"acme.a": ">=1.0"})
    e = _m(tree, "acme.e")
    plan = plan_load([a, b, d, e])
    assert [m.id for m in plan.ordered] == ["acme.e"]
    assert plan.blocked["acme.a"].startswith("blocked: dependency cycle") and "acme.b" in plan.blocked
    assert plan.blocked["acme.d"] == "blocked: dependency acme.a is blocked"


def test_builtins_keep_declaration_order_ahead_of_users():
    from narranexus.kernel.plugins.builtins import builtin_manifests

    tree = Registries().slots
    user = _m(tree, "acme.a")
    plan = plan_load([user, *builtin_manifests()])
    ids = [m.id for m in plan.ordered]
    assert ids[-1] == "acme.a" and ids[:-1] == [m.id for m in builtin_manifests()]
