"""
@file_name: test_lightweight_plugin_boot.py
@author: NarraNexus
@date: 2026-08-28
@description: The lightweight-plugin invariant — the app must import and
              register all frameworks even when the optional SDK plugins
              (claude-agent-sdk / openai-codex) are absent, and must fail
              CLOSED when a known-but-uninstalled framework is requested.

Why a subprocess: this test proves ``import narranexus.platform`` does NOT pull
``claude_agent_sdk`` at import time. The package is already imported in the
main test process, so we can only prove "boots without the SDK" in a fresh
interpreter that blocks the SDK on the meta-path. Revert the lazy-import
surgery (a top-level ``from claude_agent_sdk import ...``) and this goes red.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"

# Runs inside the blocked subprocess.
_CHILD = r"""
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
load_builtins(KERNEL_REGISTRIES, "backend")  # what every host boot does; registration is never lazy

import sys, importlib.abc

class _Block(importlib.abc.MetaPathFinder):
    _blocked = ("claude_agent_sdk", "openai_codex", "codex_cli_bin")
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in self._blocked:
            raise ImportError(f"blocked optional plugin: {name}")
        return None

sys.meta_path.insert(0, _Block())

# The whole app package must import with the plugins blocked.
import narranexus.platform  # noqa: F401
from narranexus.platform.agent_framework import (
    available_agent_loop_frameworks,
    get_agent_loop_driver,
    FrameworkNotInstalledError,
)

names = set(available_agent_loop_frameworks())
assert {"claude_code", "codex_cli", "nexus_power"} <= names, names

# Built-in framework still builds with the plugins absent.
assert get_agent_loop_driver("nexus_power") is not None

# Known-but-uninstalled framework fails CLOSED (no silent fallback).
for fw in ("claude_code", "codex_cli"):
    try:
        get_agent_loop_driver(fw)
    except FrameworkNotInstalledError as e:
        assert e.framework == fw
    else:
        raise AssertionError(f"{fw} should have raised FrameworkNotInstalledError")

print("BOOT_OK")
"""


def test_app_boots_and_fails_closed_without_plugins(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(_SRC), str(_REPO)])
    # Empty plugin home so the filesystem probe also misses — the only way a
    # blocked framework could look "installed" is a stray pyenv dir.
    env["NARRANEXUS_PLUGIN_HOME"] = str(tmp_path / "plugins")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "BOOT_OK" in proc.stdout, proc.stdout
