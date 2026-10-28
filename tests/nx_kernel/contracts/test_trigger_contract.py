"""
@file_name: test_trigger_contract.py
@author: Bin Liang
@date: 2026-09-04
@description: TriggerSpec validates its shape, resolves its class lazily, and names the class for intent checks.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import API_VERSIONS
from narranexus.contracts.trigger import Trigger, TriggerSpec


def test_trigger_kind_is_versioned():
    assert API_VERSIONS["trigger"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "", "class_ref": "a.b:C"},
        {"name": "x:y", "class_ref": "a.b:C"},
        {"name": "x", "class_ref": "a.b.C"},
        {"name": "x", "class_ref": "a.b:"},
        {"name": "x", "class_ref": "a.b:C", "host": "cron"},
    ],
)
def test_invalid_specs_fail_loud(kwargs):
    with pytest.raises(ValueError):
        TriggerSpec(**kwargs)


def test_resolve_imports_lazily_and_class_name_is_static():
    spec = TriggerSpec("lark", "narranexus_plugins.lark_module.lark_trigger:LarkTrigger")
    assert spec.class_name == "LarkTrigger" and spec.host == "channels"
    assert spec.resolve().channel_name == "lark"
    with pytest.raises(ModuleNotFoundError):
        TriggerSpec("nope", "nx.does_not_exist:Thing").resolve()
    with pytest.raises(AttributeError):
        TriggerSpec("nope", "narranexus.platform.module_system.slots:Nope").resolve()


def test_trigger_protocol_only_needs_stop():
    class T:
        async def stop(self) -> None:
            pass

    assert isinstance(T(), Trigger)
