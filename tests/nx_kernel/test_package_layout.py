"""
@file_name: test_package_layout.py
@author: Bin Liang
@date: 2026-09-03
@description: The narranexus package exists and its layering holds at import time.

import-linter enforces the same rule statically in CI; this test catches the
dynamic version (a lazy import inside a function body) that a static pass
cannot see, by importing ``narranexus.contracts`` into a clean module table
and asserting nothing from kernel / legacy / backend was pulled in.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = os.pathsep.join(str(_ROOT / p) for p in ("src", "packages/narranexus-contracts/src", "packages/narranexus-sdk/src"))

_PROBE = """
import importlib, json, sys
importlib.import_module("narranexus.contracts")
forbidden = ("narranexus.kernel", "xyz_agent_context", "backend")
leaked = sorted(n for n in sys.modules if n.startswith(forbidden))
print(json.dumps(leaked))
"""


def test_narranexus_is_a_namespace_package_spanning_engine_contracts_and_sdk():
    mod = importlib.import_module("narranexus")
    assert mod.__file__ is None and len(list(mod.__path__)) >= 2  # PEP 420: engine + packages/
    assert importlib.import_module("narranexus._version").__version__
    assert importlib.import_module("narranexus.contracts").API_VERSIONS
    assert importlib.import_module("narranexus.sdk.testing").PluginTestHost


def test_contracts_do_not_import_kernel_or_legacy():
    # A fresh interpreter: purging sys.modules in-process would re-import the
    # legacy package later and break class identity for every other test.
    env = {**os.environ, "PYTHONPATH": _SRC}
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True, timeout=120, env=env
    )
    leaked = json.loads(out.stdout.strip().splitlines()[-1])
    assert leaked == [], f"contracts pulled in non-contract modules: {leaked}"
