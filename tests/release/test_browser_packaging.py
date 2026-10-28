"""
@file_name: test_browser_packaging.py
@author:
@date: 2026-09-22
@description: Browser plugin wheel imports and optional-runtime release contract.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_browser_wheel_imports_without_playwright_or_a_browser(tmp_path):
    build = subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path)],
        cwd=ROOT / "plugins/builtin.browser", capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = next(tmp_path.glob("*.whl"))
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
        assert not any(name.endswith((".exe", ".zip", ".dylib")) for name in archive.namelist())
        metadata = archive.read(next(n for n in archive.namelist() if n.endswith("/METADATA"))).decode()
        assert "Requires-Dist: playwright" not in metadata
    code = """
import importlib.abc, importlib.resources, json, pathlib, sys
class NoPlaywright(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'playwright' or fullname.startswith('playwright.'):
            raise AssertionError('optional browser must not require playwright')
sys.meta_path.insert(0, NoPlaywright())
sys.path.insert(0, sys.argv[1])
from narranexus_plugins.browser_module import browser_module, contribution
assert pathlib.Path(browser_module.__file__).is_relative_to(sys.argv[1])
manifest = json.loads(importlib.resources.files('narranexus_plugins.browser_module').joinpath('narranexus-plugin.json').read_text())
assert manifest['id'] == contribution.PLUGIN_ID == 'builtin.browser'
assert contribution.MODULES
from narranexus.platform.browser.browser_service import BrowserService
service = BrowserService()
assert service.status().state == 'absent'
assert service.require_ready()['outcome'] == 'NEEDS_HUMAN'
assert not pathlib.Path(__import__('os').environ['NARRANEXUS_BROWSER_HOME']).exists()
print('wheel manifest, contribution and BrowserModule import with runtime absent')
"""
    env = dict(os.environ, NARRANEXUS_BROWSER_HOME=str(tmp_path / "absent"),
               NARRANEXUS_PLUGIN_HOME=str(tmp_path / "plugins"), NEXUS_DIAG_SHIP="off")
    result = subprocess.run([sys.executable, "-I", "-c", code, str(site)], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_browser_is_selected_in_desktop_and_run_sh_dependency_graph():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "narranexus-plugin-browser" in project["project"]["dependencies"]
    assert not any(dep.lower().startswith("playwright") for dep in project["project"]["dependencies"])
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert any(package["name"] == "narranexus-plugin-browser" for package in lock["package"])
    desktop = json.loads((ROOT / "distributions/desktop/narranexus-dist.json").read_text())
    assert "builtin.browser" in desktop["plugins"]


def test_browser_is_not_in_the_cloud_distribution():
    """The cloud stack cannot run it: the runtime would install into the backend
    container while sessions launch in the mcp container (no shared volume), and
    the image carries none of Chrome's system libraries. Shipping it there gives
    every cloud agent ten tools that can only answer "not installed". Enabling it
    in cloud is a cross-repo change (image, compose volume, stream secret)."""
    cloud = json.loads((ROOT / "distributions/cloud/narranexus-dist.json").read_text())
    assert "builtin.browser" not in cloud["plugins"]


def test_release_smoke_imports_browser_runtime_and_plugin():
    import runpy
    imports = runpy.run_path(str(ROOT / "scripts/release/bundle_import_smoke.py"))["LAZY_RUNTIME_IMPORTS"]
    assert "narranexus_plugins.browser_module.browser_module" in imports
    assert "narranexus.platform.browser._browser_impl.runtime_launch" in imports


def test_engine_wheel_contains_executable_manual_installer(tmp_path):
    build = subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    site = tmp_path / "site"
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as archive:
        assert "narranexus/platform/browser/__main__.py" in archive.namelist()
        archive.extractall(site)
    code = """
import pathlib, runpy, sys
sys.path.insert(0, sys.argv[1])
from narranexus.platform.browser._browser_impl import install
assert pathlib.Path(install.__file__).is_relative_to(sys.argv[1])
sys.argv = ['narranexus.platform.browser', 'status', '--root', sys.argv[2]]
runpy.run_module('narranexus.platform.browser', run_name='__main__')
"""
    root = tmp_path / "runtime absent"
    result = subprocess.run([sys.executable, "-I", "-c", code, str(site), str(root)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads(result.stdout)
    assert status["status"]["state"] == "absent"
    assert status["manual_install"]["argv"][:4] == [
        sys.executable, "-m", "narranexus.platform.browser", "install",
    ]
    assert not root.exists()
