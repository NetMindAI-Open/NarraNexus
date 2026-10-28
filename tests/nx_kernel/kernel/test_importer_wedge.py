"""
@file_name: test_importer_wedge.py
@author: Bin Liang
@date: 2026-09-07
@description: A plugin whose import hangs is SLOW (PluginImportTimeout), is failed fast on every later attempt in this process, and never blocks another plugin's import (no shared fixed-size pool).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from narranexus.kernel.plugins import importer


def _pkg(tmp_path: Path, pid: str, body: str) -> None:
    backend = tmp_path / pid / "backend"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text(body)
    importer.install_synthetic_package(pid, backend)


def test_wedged_plugins_do_not_starve_the_next_import(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(importer, "_WEDGED", {})
    for pid in ("acme.hang1", "acme.hang2"):
        _pkg(tmp_path, pid, "import time\ntime.sleep(3)\n")
    _pkg(tmp_path, "acme.quick", "OK = 1\n")
    for pid in ("acme.hang1", "acme.hang2"):
        with pytest.raises(importer.PluginImportTimeout):
            importer.import_plugin_module(pid, timeout=0.1)
    started = time.perf_counter()
    assert importer.import_plugin_module("acme.quick", timeout=5.0).OK == 1
    assert time.perf_counter() - started < 1.0  # not queued behind the two hung imports
    with pytest.raises(importer.PluginImportTimeout, match="still hung"):
        importer.import_plugin_module("acme.hang1", timeout=5.0)  # fast fail, no new thread
    for pid in ("acme.hang1", "acme.hang2", "acme.quick"):
        importer.uninstall_synthetic_package(pid)
