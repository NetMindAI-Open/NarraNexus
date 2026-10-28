"""
@file_name: test_sdk_testing_unshadow.py
@author: Bin Liang
@date: 2026-09-04
@description: PluginTestHost boots even when the plugin directory is first on sys.path and its own `backend/` package has shadowed the platform's — the situation of `pytest` run from inside a plugin directory.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from narranexus.sdk.testing import PluginTestHost


def test_plugin_backend_package_does_not_shadow_the_platform(tmp_path: Path, monkeypatch):
    plugin = tmp_path / "acme.shadow"
    (plugin / "backend").mkdir(parents=True)
    (plugin / "backend" / "__init__.py").write_text("def activate(ctx):\n    pass\n")
    (plugin / "narranexus-plugin.json").write_text(
        json.dumps({"id": "acme.shadow", "version": "1.0.0", "displayName": "Shadow", "hosts": ["backend"], "backend": {"activate": True}})
    )
    real_backend = importlib.import_module("backend").__file__
    saved = {k: v for k, v in sys.modules.items() if k == "backend" or k.startswith("backend.")}
    monkeypatch.setattr(sys, "path", [str(plugin), *sys.path])
    for k in saved:
        sys.modules.pop(k, None)
    try:
        shadow = importlib.import_module("backend")
        assert Path(shadow.__file__).is_relative_to(plugin)  # the shadow is in place
        with PluginTestHost(plugin, tmp_path / "home") as host:
            assert host.plugin_id == "acme.shadow"
            assert not host.report.builtins.errors, host.report.builtins.errors
            assert any(e.owner == "builtin.teams" for e in host.registries.registry_for("backend.routes").entries())
        assert str(plugin) not in sys.path
        assert importlib.import_module("backend").__file__ == real_backend
    finally:
        for k in [k for k in sys.modules if k == "backend" or k.startswith("backend.")]:
            del sys.modules[k]
        sys.modules.update(saved)
