"""
@file_name: test_builtin_on_demand_deps.py
@author: Bin Liang
@date: 2026-09-04
@description: A builtin with install.deps=on_demand installs its missing dependencies at boot (local build), boots without itself when the install fails or on cloud (deps_missing), and the factory can retry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from narranexus.hosts.boot import boot
from narranexus.kernel.plugins import builtins as builtins_mod
from narranexus.kernel.plugins.install import builtin_deps
from narranexus.kernel.plugins.install.deps import DepsError, DepsResult
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution

FAKE_ID = "builtin.fakedep"
MODULE = "nx_fake_on_demand_dep"

FAKE_ROUTES = (Contribution("fake", lambda: None),)


def _fake_manifest():
    return parse_manifest(
        {
            "id": FAKE_ID,
            "version": "1.0.0",
            "displayName": "Fake",
            "hosts": ["backend"],
            "provides": {"backend.routes": [f"{__name__}:FAKE_ROUTES"]},
            "api": {"route": 0},
            "backend": {"pip": ["fake-dep==1.0"], "imports": [MODULE]},
            "install": {"deps": "on_demand"},
        },
        tree=Registries().slots,
        allow_builtin=True,
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    real = builtins_mod.builtin_manifests
    fake = _fake_manifest()
    monkeypatch.setattr(builtins_mod, "builtin_manifests", lambda: (*real(), fake))
    monkeypatch.setattr(builtins_mod, "_BUILTIN_MANIFESTS_CACHE", None, raising=False)
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    target = builtin_deps.builtin_deps_target(FAKE_ID)
    yield {"store": store, "target": target, "home": home}
    sys.modules.pop(MODULE, None)
    if str(target) in sys.path:
        sys.path.remove(str(target))


def _boot(store):
    regs = Registries()
    report = boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=store)
    return regs, report


def test_missing_dependency_is_installed_at_boot_and_importable(env, monkeypatch):
    calls: list = []

    def fake_install(requirements, target, *, runner=None, **kw):
        calls.append(tuple(requirements))
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{MODULE}.py").write_text("VALUE = 42\n")
        return DepsResult(target=target, requirements=tuple(requirements), command=("fake",), output_tail="")

    monkeypatch.setattr(builtin_deps, "install_deps", fake_install)
    regs, report = _boot(env["store"])
    assert calls == [("fake-dep==1.0",)]
    assert FAKE_ID not in report.deps_missing
    assert FAKE_ID in {e.owner for e in regs.registry_for("backend.routes").entries()}
    import importlib

    assert importlib.import_module(MODULE).VALUE == 42  # the plugin-deps dir joined sys.path


def test_failed_install_boots_without_the_builtin(env, monkeypatch):
    def failing(requirements, target, *, runner=None, **kw):
        raise DepsError("no wheel for fake-dep")

    monkeypatch.setattr(builtin_deps, "install_deps", failing)
    regs, report = _boot(env["store"])
    assert "no wheel" in report.deps_missing[FAKE_ID]
    assert FAKE_ID not in {e.owner for e in regs.registry_for("backend.routes").entries()}
    assert FAKE_ID not in {pl.plugin_id for pl in report.builtins.loaded}
    assert "builtin.teams" in {pl.plugin_id for pl in report.builtins.loaded}  # the rest boots


def test_cloud_never_installs(env, monkeypatch):
    def must_not_run(*a, **kw):
        raise AssertionError("cloud boot must not install")

    monkeypatch.setattr(builtin_deps, "install_deps", must_not_run)
    regs = Registries()
    report = boot("backend", registries=regs, cloud=True, host_version="1.19.0", store=env["store"])
    assert "cloud" in report.deps_missing[FAKE_ID]


def test_present_dependency_needs_no_install(env, monkeypatch):
    env["target"].mkdir(parents=True, exist_ok=True)
    (env["target"] / f"{MODULE}.py").write_text("VALUE = 1\n")
    monkeypatch.setattr(builtin_deps, "install_deps", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not install")))
    status = builtin_deps.ensure_builtin_deps(_fake_manifest(), cloud=False)
    assert status.ok and status.on_demand and status.missing == ()


def test_lark_declares_its_sdk_on_demand():
    lark = next(m for m in builtins_mod.builtin_manifests() if m.id == "builtin.channels.lark")
    assert builtin_deps.is_on_demand(lark) and lark.backend.imports == ("lark_oapi",)
    assert builtin_deps.ensure_builtin_deps(lark, cloud=True).ok  # present in this environment
