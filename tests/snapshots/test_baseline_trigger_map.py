"""
@file_name: test_baseline_trigger_map.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the registered channel trigger set (intent + what imports here).

Runs in a subprocess (see ``tests/snapshots/_subprocess.py``):
``CHANNEL_TRIGGER_MAP`` is populated at import of
``narranexus.platform.module_system``, so an in-process read after other
tests have already imported (or monkeypatched) plugin modules is a function
of test execution order (and of `pytest -k` / xdist sharding), not of the
code alone. A fresh interpreter is the only way this snapshot is
reproducible.
"""
from __future__ import annotations

from tests.snapshots._approval import approve
from tests.snapshots._subprocess import run_probe

_PROBE = """
import json
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
load_builtins(KERNEL_REGISTRIES, "backend")  # registration happens only at boot: the probe boots like a host
from narranexus.platform.module_system.channel_trigger_map import (
    CHANNEL_TRIGGER_MAP,
    registered_trigger_class_names,
)

print(json.dumps({
    "registered_class_names": sorted(registered_trigger_class_names()),
    "loaded_channels": sorted(CHANNEL_TRIGGER_MAP),
}))
"""


def test_channel_trigger_registration_is_unchanged():
    view = run_probe(_PROBE, env={"NARRANEXUS_DEPLOYMENT_MODE": "local"})
    approve("trigger_map", view)
