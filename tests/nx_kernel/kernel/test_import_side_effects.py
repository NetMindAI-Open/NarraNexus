"""
@file_name: test_import_side_effects.py
@author: Bin Liang
@date: 2026-09-07
@description: Importing is not registering, and reading a manifest is not importing a plugin.

Two invariants the whole batch rests on, each of which was silently violated
once already and neither of which any AST gate can see (both violations went
through ``importlib.resources.files(...)`` / module-level ``register(...)``,
which import-linter reads as strings and imports it approves of):

- **M1 — registration happens at boot, nowhere else.** If merely importing a
  platform package or a builtin plugin package registers a contribution, a
  distribution's ``excludes`` is cosmetic (the handler is registered before
  ``load_builtins`` ever filters), and the registry contents depend on import
  order rather than on the manifests.
- **G2-C1 — reading the builtin manifests must not import the builtin code.**
  ``importlib.resources.files("narranexus_plugins.x")`` imports the package it
  is asked about, so reading 29 JSON files pulled 106 plugin modules (~1.2 s)
  into every process that touched the kernel — including hosts whose
  distribution excludes those plugins.

Both run in a clean subprocess: an in-process assertion would be meaningless
because pytest's own collection has already imported half the tree.
"""
from __future__ import annotations

from tests.snapshots._subprocess import run_probe

_PROBE_MANIFESTS = """
import json, sys
import narranexus.kernel.plugins.builtins as b
after_import = sorted(m for m in sys.modules if m.startswith("narranexus_plugins"))
data = b.builtin_manifest_data()
after_read = sorted(m for m in sys.modules if m.startswith("narranexus_plugins"))
print(json.dumps({"after_import": after_import, "after_read": after_read, "manifests": len(data)}))
"""

_PROBE_REGISTRATION = """
import importlib, json, pkgutil, sys
import narranexus.platform as platform
import narranexus_plugins as plugins
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

imported = []
for package in (platform, plugins):
    for info in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        try:
            importlib.import_module(info.name)
        except Exception as exc:  # a package that cannot import here is not this test's subject
            print(f"skipped {info.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        imported.append(info.name)

hooks = sorted(name for name in KERNEL_REGISTRIES.hooks.names() if KERNEL_REGISTRIES.hooks.caller(name).owners())
print(json.dumps({
    "imported": len(imported),
    "snapshot": KERNEL_REGISTRIES.snapshot(),
    "hooks_with_impls": hooks,
    "frozen": KERNEL_REGISTRIES.frozen,
}))
"""


def test_reading_the_builtin_manifests_imports_no_plugin_code():
    out = run_probe(_PROBE_MANIFESTS, env={})
    assert out["manifests"] >= 29
    # Importing the kernel's builtins module reads nothing at all (the manifest
    # data is behind an lru_cache function, not a module-level constant).
    assert out["after_import"] == []
    # Reading them locates each package instead of importing it. The bare
    # namespace package is the one permitted entry: it holds no code.
    assert out["after_read"] == ["narranexus_plugins"], out["after_read"]


def test_importing_the_platform_and_the_builtin_packages_registers_nothing():
    out = run_probe(_PROBE_REGISTRATION, env={})
    assert out["imported"] >= 20, out["imported"]  # the loop must have had something to import
    assert out["snapshot"] == {}, out["snapshot"]
    assert out["hooks_with_impls"] == [], out["hooks_with_impls"]
    assert out["frozen"] is False
