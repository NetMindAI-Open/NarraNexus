"""
@file_name: test_baseline_workers.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the worker supervisor's canonical worker set and startup order.

Runs in a subprocess (see ``tests/snapshots/_subprocess.py``): ``build_specs``
depends on which plugin contributions the process has already registered, so
an in-process read is a function of test execution order (and of `pytest -k`
/ xdist sharding), not of the code alone. A fresh interpreter is the only way
this snapshot is reproducible.
"""
from __future__ import annotations

from tests.snapshots._approval import approve
from tests.snapshots._subprocess import run_probe

_PROBE = """
import json
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.platform.module_system.run_worker_supervisor import ALL_WORKERS, build_specs

regs = Registries()
load_builtins(regs, "backend")
print(json.dumps({"order": list(ALL_WORKERS), "specs": sorted(s.name for s in build_specs(registries=regs))}))
"""


def test_worker_specs_are_unchanged():
    """The effective builtin worker set: platform workers plus builtin trigger workers ("jobs" since 3c.3)."""
    view = run_probe(_PROBE, env={"NARRANEXUS_DEPLOYMENT_MODE": "local"})
    approve("workers", view)
