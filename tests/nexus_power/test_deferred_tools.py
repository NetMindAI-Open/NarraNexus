"""
@file_name: test_deferred_tools.py
@author: Bin Liang
@date: 2026-09-03
@description: Deferred tools stay searchable and callable but leave the model's up-front tool list.
"""
from __future__ import annotations

from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.dispatcher import ToolDispatcher
from narranexus_plugins.frameworks_nexus_power.core.contracts.tooling import ToolSpec


class _Channel:
    generation = 0

    def __init__(self, *specs):
        self._specs = specs

    def list_tools(self):
        return list(self._specs)


class _Policy:
    def check(self, call, ctx):  # pragma: no cover - not exercised
        raise AssertionError


def _dispatcher(deferred):
    return ToolDispatcher(
        (_Channel(ToolSpec(name="a", description="", input_schema={}), ToolSpec(name="b", description="", input_schema={})),),
        policy=_Policy(),
        ctx=None,
        disallowed_tools=frozenset(),
        allowed_tools=frozenset(),
        marker_tools=frozenset(),
        deferred_tools=frozenset(deferred),
    )


def test_model_tools_excludes_deferred_but_visible_and_spec_for_keep_them():
    d = _dispatcher({"b"})
    assert [s.name for s in d.visible_tools()] == ["a", "b"]
    assert [s.name for s in d.model_tools()] == ["a"]
    assert d.spec_for("b") is not None


def test_no_deferred_means_model_tools_equals_visible():
    d = _dispatcher(set())
    assert [s.name for s in d.model_tools()] == ["a", "b"]
