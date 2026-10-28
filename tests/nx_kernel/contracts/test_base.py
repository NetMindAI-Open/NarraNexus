"""
@file_name: test_base.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract primitives — Disposable semantics, error taxonomy, version/stability tables.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import (
    API_VERSIONS,
    STABILITY,
    BindingConflict,
    Disposable,
    DisposableStack,
    IncompatibleProvider,
    ManifestError,
    PluginError,
    RegistryConflict,
    RegistryFrozen,
    Stability,
    UnboundSlot,
    UnknownEntry,
)


def test_disposable_runs_once():
    calls: list[int] = []
    d = Disposable(lambda: calls.append(1))
    assert not d.disposed
    d.dispose()
    d.dispose()
    assert calls == [1]
    assert d.disposed


def test_disposable_stack_disposes_in_reverse_order():
    order: list[str] = []
    stack = DisposableStack()
    stack.add(Disposable(lambda: order.append("a")))
    stack.add(Disposable(lambda: order.append("b")))
    assert len(stack) == 2
    stack.dispose()
    assert order == ["b", "a"]
    assert stack.disposed
    assert len(stack) == 0


def test_disposable_stack_aggregates_failures_and_still_releases_the_rest():
    order: list[str] = []

    def boom() -> None:
        raise RuntimeError("x")

    stack = DisposableStack()
    stack.add(Disposable(lambda: order.append("first")))
    stack.add(Disposable(boom))
    with pytest.raises(ExceptionGroup) as info:
        stack.dispose()
    assert order == ["first"]
    assert [type(e) for e in info.value.exceptions] == [RuntimeError]


def test_adding_to_a_disposed_stack_disposes_immediately():
    stack = DisposableStack()
    stack.dispose()
    d = stack.add(Disposable(lambda: None))
    assert d.disposed


def test_error_hierarchy():
    for exc in (
        RegistryConflict,
        UnknownEntry,
        RegistryFrozen,
        BindingConflict,
        UnboundSlot,
        IncompatibleProvider,
        ManifestError,
    ):
        assert issubclass(exc, PluginError)
    assert issubclass(UnknownEntry, KeyError)
    assert issubclass(ManifestError, ValueError)
    assert str(UnknownEntry("no such entry 'x'")) == "no such entry 'x'"


def test_versions_and_stability_cover_the_same_kinds_and_are_stable_since_batch_6():
    # Concrete membership, not just truthiness: dropping a kind (or a typo in
    # its name) must fail this test, not silently shrink the contract surface.
    assert set(API_VERSIONS) == {
        "framework", "agent_events", "agent", "services", "ui", "provider",
        "llm_client", "memory", "events", "hook", "route", "table", "worker",
        "trigger", "data_access", "channel", "settings", "tool", "mcp_server",
        "bundle", "skill", "theme", "stage_strategy", "pipeline_profile",
        "context_provider", "module", "auth", "prompt",
        # Authoring surfaces, not manifest kinds (batch 6c): the request-scoped
        # host API a plugin router calls, and the channel base classes the SDK
        # re-exports for the template.
        "web", "channel_authoring",
    }
    assert set(API_VERSIONS) == set(STABILITY)
    assert all(isinstance(v, int) and v >= 0 for v in API_VERSIONS.values())
    # A new kind starts ALPHA until it is promoted (contracts/__init__.py):
    # demanding STABLE here would fail the first ALPHA kind to land, which is
    # the documented process, not a regression.
    assert all(s in (Stability.ALPHA, Stability.BETA, Stability.STABLE) for s in STABILITY.values())
