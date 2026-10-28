"""
@file_name: test_bound_framework_loud.py
@author: Bin Liang
@date: 2026-09-07
@description: A turn.pipeline.act.framework binding that names a framework nobody registered is a FrameworkNotInstalledError, never a silent nexus_power fallback; an UNBOUND slot still means the code default.
"""
from __future__ import annotations

import pytest

from narranexus.kernel.plugins.bindings import Bound, Layer, ResolvedBindings
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.platform.agent_framework.loop import driver as drv


def test_unbound_slot_means_the_code_default(monkeypatch):
    monkeypatch.setattr(KERNEL_REGISTRIES, "_bindings", None, raising=False)
    assert drv.bound_default_framework() == drv.DEFAULT_AGENT_LOOP_FRAMEWORK


def test_binding_to_a_missing_framework_is_loud(monkeypatch):
    resolved = ResolvedBindings(one={"turn.pipeline.act.framework": Bound("acme.no_such_framework", Layer.ENV, origin="env")})
    monkeypatch.setattr(KERNEL_REGISTRIES, "_bindings", resolved, raising=False)
    with pytest.raises(drv.FrameworkNotInstalledError, match="acme.no_such_framework"):
        drv.bound_default_framework()


def test_binding_to_a_registered_framework_resolves_its_name(monkeypatch):
    resolved = ResolvedBindings(one={"turn.pipeline.act.framework": Bound("builtin.frameworks.nexus_power", Layer.ENV, origin="env")})
    monkeypatch.setattr(KERNEL_REGISTRIES, "_bindings", resolved, raising=False)
    assert drv.bound_default_framework() == "nexus_power"
