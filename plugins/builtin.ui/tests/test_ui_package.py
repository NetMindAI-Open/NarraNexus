"""
@file_name: test_ui_package.py
@author: Bin Liang
@date: 2026-09-07
@description: Package contract of `builtin.ui`: the manifest declares exactly one ui.* slot per frontend registry (the eighteen names in frontend/src/platform/registries/index.ts), every declared contract resolves to a narranexus.contracts.ui symbol, and the slots reach the tree owned by builtin.ui.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def _frontend_registry_names() -> set[str]:
    src = (REPO / "frontend" / "src" / "platform" / "registries" / "index.ts").read_text()
    block = src.split("export const REGISTRIES = {", 1)[1].split("} as const;", 1)[0]
    return {re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), key) for key in re.findall(r"^\s*(\w+):", block, flags=re.M)}


def test_manifest_declares_one_slot_per_frontend_registry():
    manifest = json.loads((ROOT / "narranexus-plugin.json").read_text())
    declared = {path.removeprefix("ui.") for path in manifest["declares"]}
    assert declared == _frontend_registry_names()
    for path, decl in manifest["declares"].items():
        module_path, _, symbol = decl["contract"].partition(":")
        assert hasattr(importlib.import_module(module_path), symbol), path
        assert decl["kind"] in ("ui", "theme")


def test_slots_are_owned_by_builtin_ui_in_the_tree():
    from narranexus.kernel.plugins.builtins import slot_tree_with_builtins

    tree = slot_tree_with_builtins()
    assert tree.get("ui").default == "builtin.ui"
    assert tree.get("ui.themes").owner == "builtin.ui" and tree.get("ui.themes").kind == "theme"
    assert {s.path for s in tree.children("ui")} == {"ui." + n for n in _frontend_registry_names()}
