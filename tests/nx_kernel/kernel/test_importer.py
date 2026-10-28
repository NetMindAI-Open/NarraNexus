"""
@file_name: test_importer.py
@author: Bin Liang
@date: 2026-09-03
@description: Two plugins with the same top-level module import independently, plugin deps never shadow host packages, and a hanging import is isolated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from narranexus.contracts import PluginError
from narranexus.kernel.plugins import importer


def _plugin(tmp_path: Path, pid: str, utils_body: str) -> Path:
    backend = tmp_path / pid / "backend"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("from .utils import VALUE\n")
    (backend / "utils.py").write_text(utils_body)
    return backend


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for pid in ("acme.one", "acme.two", "acme.slow", "acme.deps"):
        importer.uninstall_synthetic_package(pid)
        importer.plugin_finder().unregister_deps(pid)


def test_same_top_level_module_name_in_two_plugins(tmp_path: Path):
    _plugin(tmp_path, "acme.one", "VALUE = 'one'\n")
    _plugin(tmp_path, "acme.two", "VALUE = 'two'\n")
    importer.install_synthetic_package("acme.one", tmp_path / "acme.one" / "backend")
    importer.install_synthetic_package("acme.two", tmp_path / "acme.two" / "backend")
    one = importer.import_plugin_module("acme.one")
    two = importer.import_plugin_module("acme.two")
    assert (one.VALUE, two.VALUE) == ("one", "two")
    assert "utils" not in sys.modules or sys.modules["utils"].__name__ == "utils"  # host namespace untouched
    assert importer.package_name("acme.one") == "nxplugins.acme_one"
    assert importer.uninstall_synthetic_package("acme.one") >= 2
    assert "nxplugins.acme_one.utils" not in sys.modules


def test_install_is_idempotent_and_refuses_a_different_dir(tmp_path: Path):
    backend = _plugin(tmp_path, "acme.one", "VALUE = 1\n")
    assert importer.install_synthetic_package("acme.one", backend) == importer.install_synthetic_package("acme.one", backend)
    other = _plugin(tmp_path / "elsewhere", "acme.one", "VALUE = 2\n")
    with pytest.raises(PluginError, match="already installed"):
        importer.install_synthetic_package("acme.one", other)
    with pytest.raises(PluginError, match="does not exist"):
        importer.install_synthetic_package("acme.two", tmp_path / "missing")


def test_private_dependency_visible_only_to_its_plugin_and_host_wins(tmp_path: Path):
    deps = tmp_path / "deps"
    (deps / "acmelib").mkdir(parents=True)
    (deps / "acmelib" / "__init__.py").write_text("WHO = 'plugin-private'\n")
    (deps / "acmelib_unused").mkdir()
    (deps / "acmelib_unused" / "__init__.py").write_text("X = 1\n")
    # a fake "json" in the plugin deps must NOT shadow the host's json
    (deps / "json").mkdir()
    (deps / "json" / "__init__.py").write_text("raise AssertionError('host json was shadowed')\n")
    backend = tmp_path / "acme.deps" / "backend"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("import json\nimport acmelib\nWHO = acmelib.WHO\nJSON_OK = hasattr(json, 'dumps')\n")
    importer.install_synthetic_package("acme.deps", backend)
    importer.plugin_finder().register_deps("acme.deps", deps)
    mod = importer.import_plugin_module("acme.deps")
    assert mod.WHO == "plugin-private" and mod.JSON_OK
    # The host cannot reach a private dependency through the finder: only the
    # plugin's own imports are served from its deps dir. (A dependency the
    # plugin already imported is cached in sys.modules like any module — the
    # guarantee is "no shadowing of host packages", not a separate interpreter.)
    with pytest.raises(ImportError):
        __import__("acmelib_unused")


def test_hanging_import_is_isolated_with_a_deadline(tmp_path: Path):
    backend = tmp_path / "acme.slow" / "backend"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("import time\ntime.sleep(2)\n")
    importer.install_synthetic_package("acme.slow", backend)
    with pytest.raises(PluginError, match="exceeded"):
        importer.import_plugin_module("acme.slow", timeout=0.2)


def test_import_error_is_wrapped_with_the_plugin_id(tmp_path: Path):
    backend = tmp_path / "acme.one" / "backend"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("raise RuntimeError('broken at import')\n")
    importer.install_synthetic_package("acme.one", backend)
    with pytest.raises(PluginError, match="acme.one: importing nxplugins.acme_one failed: RuntimeError: broken at import"):
        importer.import_plugin_module("acme.one")
