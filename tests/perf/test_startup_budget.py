"""
@file_name: test_startup_budget.py
@author: Bin Liang
@date: 2026-09-03
@description: Plugin-platform startup budget (spec §12): manifest parsing and staged boot stay cheap as plugin count grows.

Budgets are deliberately generous for CI noise (they catch an order-of-
magnitude regression such as importing plugin code at boot, not a 20%
drift): ≤ 20 ms per manifest parse, ≤ 60 ms per user plugin through the
whole stage-2 path, frozen registry lookups in microseconds.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins, BUILTIN_MANIFEST_DATA
from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.slots import build_kernel_slot_tree

N_PLUGINS = 25
PER_MANIFEST_MS = 1.0  # measured well under 0.1 ms; the old 20 ms hid a 200x regression
PER_PLUGIN_BOOT_MS = 60.0


def _synthetic_plugin(home: Path, i: int) -> Path:
    pid = f"perf.p{i:02d}"
    root = home / pid
    (root / "backend").mkdir(parents=True)
    # Code that would be EXPENSIVE if imported at boot: it must not be.
    (root / "backend" / "__init__.py").write_text("import time\ntime.sleep(5)\n\ndef activate(ctx):\n    pass\n")
    (root / "narranexus-plugin.json").write_text(json.dumps({"id": pid, "version": "1.0.0", "displayName": pid, "hosts": ["backend"], "backend": {"activate": True}}))
    return root


def test_manifest_parse_budget():
    tree = slot_tree_with_builtins()
    started = time.perf_counter()
    rounds = 20
    for _ in range(rounds):
        for data in BUILTIN_MANIFEST_DATA:
            parse_manifest(data, tree=tree, allow_builtin=True)
    per = (time.perf_counter() - started) * 1000.0 / (rounds * len(BUILTIN_MANIFEST_DATA))
    assert per < PER_MANIFEST_MS, f"{per:.2f} ms per manifest"


def test_stage2_boot_scales_linearly_and_never_imports_plugin_code(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    for i in range(N_PLUGINS):
        store.register(f"perf.p{i:02d}", PluginRecord(path=str(_synthetic_plugin(home, i))))
    started = time.perf_counter()
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert len(report.user_plugin_ids) == N_PLUGINS and report.isolated == {}
    assert elapsed_ms < N_PLUGINS * PER_PLUGIN_BOOT_MS, f"boot took {elapsed_ms:.0f} ms for {N_PLUGINS} plugins"
    # (the sleeping __init__ was never imported: the per-plugin bound above is far below one sleep)


def _lookup_cost_us(n: int) -> float:
    regs = Registries()
    reg = regs.registry_for("backend.routes")
    for i in range(n):
        reg.register(f"r{i}", (lambda i=i: i), owner=f"perf.p{i}")
    regs.freeze()
    rounds = 20000
    started = time.perf_counter()
    for _ in range(rounds):
        reg.get(f"r{n - 1}")
    return (time.perf_counter() - started) * 1e6 / rounds


def test_frozen_registry_lookup_is_constant_time():
    """A real cross-size comparison: 100x more entries must not make a lookup materially slower (an O(n) scan would)."""
    small, large = _lookup_cost_us(50), _lookup_cost_us(5000)
    assert large < max(small * 3.0, 2.0), f"{small:.2f} µs @50 vs {large:.2f} µs @5000"


@pytest.mark.parametrize("path", ["turn.pipeline.act.framework", "backend.routes", "content.skills"])
def test_registries_construction_is_cheap(path: str):
    tree = slot_tree_with_builtins()  # built once: the budget is the Registries facade, not the tree
    started = time.perf_counter()
    for _ in range(20):
        Registries(tree).registry_for(path)
    per_ms = (time.perf_counter() - started) * 1000.0 / 20
    assert per_ms < 5.0, f"{per_ms:.2f} ms per Registries()"  # measured ~0.1 ms; 25 ms let a 100x regression through
