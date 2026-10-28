"""
@file_name: test_subprocess_env.py
@author: Bin Liang
@date: 2026-09-08
@description: A child interpreter imports nxplugins.<id> through the generated bootstrap package.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.subprocess_env import bootstrap_dir, ensure_bootstrap, subprocess_env


def _plugin(home: Path, pid: str) -> Path:
    root = home / pid
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "__init__.py").write_text("ANSWER = 42\n")
    (root / "backend" / "server.py").write_text("from nxplugins.acme_x import ANSWER\nprint('answer', ANSWER)\n")
    (root / "narranexus-plugin.json").write_text(json.dumps({"id": pid, "version": "1.0.0", "displayName": pid, "minAppVersion": "0.0.0", "hosts": ["backend"]}))
    return root


def test_child_process_imports_the_plugin_package(tmp_path: Path, monkeypatch):
    """This is the tool template's stdio server, executed for real: a fresh
    interpreter running ``-m nxplugins.<pkg>.server`` used to die with
    "No module named nxplugins" because the namespace only existed as an
    in-process finder (2026-09-08 local e2e: the agent's tool never appeared)."""
    home = tmp_path / "plugins"
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    root = _plugin(home, "acme.x")
    RegistryStore(path=home / "registry.json", lkg=home / "lkg.json").register("acme.x", PluginRecord(path=str(root), installed_version="1.0.0"))

    env = subprocess_env({"EXTRA": "1"})
    assert env["PYTHONPATH"] == str(bootstrap_dir()) and env[ENV_PLUGIN_HOME] == str(home) and env["EXTRA"] == "1"
    assert (bootstrap_dir() / "nxplugins" / "__init__.py").is_file()

    proc = subprocess.run(
        [sys.executable, "-m", "nxplugins.acme_x.server"],
        capture_output=True, text=True, timeout=120, env={**os.environ, **env},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "answer 42"


def test_bootstrap_is_idempotent_and_prepends_a_caller_pythonpath(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(tmp_path / "plugins"))
    first = ensure_bootstrap()
    stamp = (first / "nxplugins" / "__init__.py").stat().st_mtime_ns
    assert ensure_bootstrap() == first
    assert (first / "nxplugins" / "__init__.py").stat().st_mtime_ns == stamp  # unchanged content is not rewritten
    env = subprocess_env({"PYTHONPATH": "/somewhere/else"})
    assert env["PYTHONPATH"].split(os.pathsep) == [str(first), "/somewhere/else"]
