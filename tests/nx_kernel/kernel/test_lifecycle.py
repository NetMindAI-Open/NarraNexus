"""
@file_name: test_lifecycle.py
@author: Bin Liang
@date: 2026-09-03
@description: registry.json writes are atomic with an LKG snapshot, the state machine is enforced, crashes auto-disable, and the boot marker flips safe mode.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from narranexus.kernel.plugins import lifecycle as lc


@pytest.fixture
def store(tmp_path: Path) -> lc.RegistryStore:
    return lc.RegistryStore(path=tmp_path / "registry.json", lkg=tmp_path / "registry.lkg.json")


def _record(path="/p/acme.weather", **kw) -> lc.PluginRecord:
    return lc.PluginRecord(path=path, installed_version="1.0.0", **kw)


def test_register_writes_atomically_and_lkg_moves_only_on_snapshot(store: lc.RegistryStore, tmp_path: Path):
    assert store.read() == lc.RegistryFile()
    store.register("acme.weather", _record())
    data = json.loads(store.path.read_text())
    assert data["schema"] == 1 and data["plugins"]["acme.weather"]["state"] == "registered"
    assert data["plugins"]["acme.weather"]["installed_at"]
    # writes do NOT move the last-known-good copy: a boot writes too, and the
    # LKG must be the last state that reached HEALTH, not the last one written
    store.transition("acme.weather", "validated")
    assert store.read_lkg() is None
    assert store.snapshot_lkg() is True
    store.transition("acme.weather", "enabled")
    lkg = store.read_lkg()
    assert lkg is not None and lkg.plugins["acme.weather"].state == "validated"
    assert store.read().plugins["acme.weather"].state == "enabled"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("registry.json.") and not p.name.endswith(".lock")]


def test_corrupt_file_and_duplicate_register_fail_loud(store: lc.RegistryStore):
    store.path.write_text("{not json")
    with pytest.raises(lc.RegistryError, match="corrupt"):
        store.read()
    store.path.unlink()
    store.register("acme.weather", _record())
    with pytest.raises(lc.RegistryError, match="already registered"):
        store.register("acme.weather", _record())


def test_happy_path_and_invalid_transitions(store: lc.RegistryStore):
    store.register("acme.weather", _record())
    with pytest.raises(lc.RegistryError):
        store.transition("acme.weather", "active")  # skips validated/enabled
    store.transition("acme.weather", "validated")
    store.transition("acme.weather", "enabled")
    rec = store.transition("acme.weather", "active")
    assert rec.activated_at and rec.last_error is None
    rec = store.transition("acme.weather", "deps_missing", error="httpx not installed")
    assert rec.last_error == "httpx not installed"
    with pytest.raises(lc.RegistryError, match="re-validate"):
        store.transition("acme.weather", "enabled")
    assert store.transition("acme.weather", "registered").last_error is None
    with pytest.raises(lc.RegistryError, match="unknown"):
        store.transition("nope", "validated")


def test_second_crash_auto_disables_and_enable_resets(store: lc.RegistryStore):
    store.register("acme.weather", _record())
    rec = store.record_crash("acme.weather", "boom 1")
    assert rec.state == "crashed" and rec.enabled and rec.crash_count == 1
    rec = store.record_crash("acme.weather", "boom 2")
    assert rec.state == "disabled" and not rec.enabled and "auto-disabled" in rec.warnings[0]
    assert store.read().enabled_ids() == []
    rec = store.set_enabled("acme.weather", True)
    assert rec.state == "registered" and rec.crash_count == 0 and rec.enabled
    assert store.read().enabled_ids() == ["acme.weather"]


def test_rollback_to_lkg_is_a_file_operation(store: lc.RegistryStore):
    store.register("acme.weather", _record())
    store.register("acme.other", _record("/p/other"))
    store.snapshot_lkg()  # the state a healthy boot proved
    store.unregister("acme.other")
    assert "acme.other" not in store.read().plugins
    restored = store.rollback_to_lkg()
    assert "acme.other" in restored.plugins and "acme.other" in store.read().plugins
    store.lkg.unlink()
    with pytest.raises(lc.RegistryError, match="no last-known-good"):
        store.rollback_to_lkg()


def test_safe_mode_flag_and_scope_survive_round_trip(store: lc.RegistryStore):
    store.register("me.tool", _record(scope="agent:agent7", mode="link", installed_by="agent:agent7"))
    store.set_safe_mode(True, reason="2 failed boots")
    reg = store.read()
    assert reg.safe_mode and reg.safe_mode_reason == "2 failed boots"
    assert reg.plugins["me.tool"].scope == "agent:agent7" and reg.plugins["me.tool"].mode == "link"
    assert not store.set_safe_mode(False).safe_mode


def test_boot_marker_counts_consecutive_failures(tmp_path: Path):
    marker = lc.BootMarker("backend", path=tmp_path / ".booting-backend")
    assert marker.enter() == 0 and not marker.safe_mode_due
    marker.exit()
    assert marker.consecutive_failures() == 0
    # crash: enter without exit, twice
    assert marker.enter() == 0
    assert marker.enter() == 1 and not marker.safe_mode_due
    assert marker.enter() == 2 and marker.safe_mode_due
    marker.exit()
    assert not marker.path.exists()


def test_atomic_write_leaves_no_temp_file_on_failure(store: lc.RegistryStore, monkeypatch):
    store.register("acme.weather", _record())
    calls = {"n": 0}
    real_replace = os.replace

    def _boom(src, dst):
        calls["n"] += 1
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        store.set_enabled("acme.weather", False)
    monkeypatch.setattr(os, "replace", real_replace)
    assert store.read().plugins["acme.weather"].enabled is True  # old file intact
    assert not [p for p in store.path.parent.iterdir() if p.name.startswith("registry.json.") and not p.name.endswith(".lock")]
