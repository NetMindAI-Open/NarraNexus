"""
@file_name: test_route_mounting.py
@author:
@date: 2026-09-23
@description: Verify actual backend imports honor the browser plugin switch.
"""
import json
import os
import subprocess
import sys

import pytest

from narranexus.kernel.plugins.lifecycle import RegistryStore


@pytest.mark.parametrize("enabled", [True, False])
def test_backend_mounts_browser_routes_only_when_plugin_loaded(tmp_path, enabled):
    home = tmp_path / "plugins"
    home.mkdir()
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    if not enabled:
        store.update(lambda registry: registry.builtin_overrides.__setitem__("builtin.browser", {"enabled": False}))
    env = {**os.environ, "NARRANEXUS_PLUGIN_HOME": str(home),
           "PYTHONPATH": os.pathsep.join(sys.path), "NEXUS_DIAG_SHIP": "off"}
    result = subprocess.run([
        sys.executable, "-c",
        "import json; from backend.main import app; "
        "print(json.dumps(sorted(r.path for r in app.routes if hasattr(r, 'path') "
        "and ('/api/browser/' in r.path or '/ws/browser/' in r.path))))",
    ], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-3000:]
    paths = json.loads(result.stdout.strip().splitlines()[-1])
    assert bool(paths) is enabled
    if enabled:
        assert "/ws/browser/{agent_id}" in paths
        assert "/api/browser/runtime" in paths
