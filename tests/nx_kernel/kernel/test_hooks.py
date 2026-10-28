"""
@file_name: test_hooks.py
@author: Bin Liang
@date: 2026-09-03
@description: Hook mechanics — pluggy ordering, wrappers, firstresult, arg pruning, blocking, isolation.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import RegistryConflict, RegistryFrozen, UnknownEntry
from narranexus.kernel.plugins.hooks import HookCaller, HookRegistry, HookSpec

SPEC = HookSpec("onDidThing", params=("a", "b"))


async def test_lifo_order_with_tryfirst_and_trylast():
    caller = HookCaller(SPEC)
    order: list[str] = []
    caller.add(lambda a: order.append("first-registered") or "r1", owner="p1")
    caller.add(lambda a: order.append("second-registered") or "r2", owner="p2")
    caller.add(lambda a: order.append("last") or "r3", owner="p3", trylast=True)
    caller.add(lambda a: order.append("first") or "r4", owner="p4", tryfirst=True)
    outcome = await caller.call(a=1, b=2)
    assert order == ["first", "second-registered", "first-registered", "last"]
    assert outcome.results == ["r4", "r2", "r1", "r3"]
    assert outcome.errors == []


async def test_firstresult_stops_at_first_non_none():
    caller = HookCaller(HookSpec("onDidPick", params=("x",), firstresult=True))
    calls: list[str] = []
    caller.add(lambda x: calls.append("a") or None, owner="a")
    caller.add(lambda x: calls.append("b") or "B", owner="b")
    caller.add(lambda x: calls.append("c") or "C", owner="c")
    outcome = await caller.call(x=0)
    # LIFO: c runs first and wins; b and a never run.
    assert outcome.first == "C" and calls == ["c"]


async def test_implementation_declares_only_the_params_it_uses():
    caller = HookCaller(SPEC)
    caller.add(lambda b: b * 2, owner="p")
    caller.add(lambda **kw: sorted(kw), owner="q")
    outcome = await caller.call(a=1, b=21)
    assert outcome.results == [["a", "b"], 42]


def test_implementation_requiring_unknown_param_is_rejected_at_add_time():
    caller = HookCaller(SPEC)
    with pytest.raises(TypeError, match="requires parameters \\['zzz'\\]"):
        caller.add(lambda a, zzz: None, owner="p")


async def test_missing_call_argument_is_a_caller_bug():
    caller = HookCaller(SPEC)
    caller.add(lambda a: a, owner="p")
    with pytest.raises(TypeError, match="missing call arguments \\['b'\\]"):
        await caller.call(a=1)


async def test_async_and_sync_impls_mix_and_errors_are_isolated_per_owner():
    caller = HookCaller(SPEC)

    async def slow(a):
        return a + 1

    def boom(a):
        raise ValueError("bad")

    caller.add(slow, owner="async-plugin")
    caller.add(boom, owner="broken-plugin")
    caller.add(lambda a: a + 2, owner="sync-plugin")
    outcome = await caller.call(a=1, b=0)
    assert outcome.results == [3, 2]
    assert [(o, type(e)) for o, e in outcome.errors] == [("broken-plugin", ValueError)]


async def test_wrapper_yields_once_and_observes_the_outcome():
    caller = HookCaller(SPEC)
    seen: list[str] = []

    def wrapper(a):
        seen.append("before")
        outcome = yield
        seen.append(f"after:{outcome.results}")

    async def async_wrapper(a):
        seen.append("abefore")
        outcome = yield
        seen.append(f"aafter:{len(outcome.results)}")

    caller.add(wrapper, owner="w", wrapper=True)
    caller.add(async_wrapper, owner="aw", wrapper=True)
    caller.add(lambda a: "x", owner="p")
    outcome = await caller.call(a=1, b=2)
    assert outcome.results == ["x"]
    # LIFO: async wrapper (registered last) is outermost.
    assert seen == ["abefore", "before", "after:['x']", "aafter:1"]


async def test_wrapper_that_yields_twice_is_reported_not_fatal():
    caller = HookCaller(SPEC)

    def greedy(a):
        yield
        yield

    caller.add(greedy, owner="greedy", wrapper=True)
    outcome = await caller.call(a=1, b=2)
    assert [o for o, _ in outcome.errors] == ["greedy"]


def test_wrapper_must_be_a_generator_and_flags_are_exclusive():
    caller = HookCaller(SPEC)
    with pytest.raises(TypeError, match="must be a generator"):
        caller.add(lambda a: None, owner="p", wrapper=True)
    with pytest.raises(ValueError, match="exclusive"):
        caller.add(lambda a: None, owner="p", tryfirst=True, trylast=True)


async def test_block_removes_every_impl_of_an_owner_and_dispose_removes_one():
    caller = HookCaller(SPEC)
    d = caller.add(lambda a: "keep", owner="good")
    caller.add(lambda a: "x", owner="bad")
    caller.add(lambda a: "y", owner="bad")
    assert caller.block("bad") == 2 and caller.owners() == ("good",)
    d.dispose()
    assert len(caller) == 0
    assert (await caller.call(a=1, b=2)).results == []


async def test_failed_wrapper_before_half_is_closed_so_finally_runs():
    caller = HookCaller(SPEC)
    closed: list[str] = []

    def bad_wrapper(a):
        try:
            raise RuntimeError("before failed")
            yield  # unreachable on purpose: makes this a generator
        finally:
            closed.append("sync")

    async def bad_async_wrapper(a):
        try:
            raise RuntimeError("before failed")
            yield
        finally:
            closed.append("async")

    caller.add(bad_wrapper, owner="w1", wrapper=True)
    caller.add(bad_async_wrapper, owner="w2", wrapper=True)
    outcome = await caller.call(a=1, b=2)
    assert sorted(o for o, _ in outcome.errors) == ["w1", "w2"]
    assert sorted(closed) == ["async", "sync"]


def test_freeze_stops_adding_implementations_but_declaring_stays_frozen_too():
    reg = HookRegistry()
    reg.declare(SPEC)
    reg.freeze()
    assert reg.frozen and reg.caller("onDidThing").frozen
    with pytest.raises(RegistryFrozen):
        reg.add("onDidThing", lambda a: a, owner="late")
    late = reg.declare(HookSpec("onDidLate", params=()))
    assert late.frozen


async def test_registry_declares_specs_and_dispatches_by_name():
    reg = HookRegistry()
    reg.declare(SPEC)
    assert reg.declare(SPEC) is reg.caller(SPEC.name)  # identical spec: idempotent
    with pytest.raises(RegistryConflict, match="different signature"):
        reg.declare(HookSpec(SPEC.name, SPEC.params + ("extra",)))
    with pytest.raises(UnknownEntry, match="not declared"):
        reg.caller("onDidNothing")
    reg.add("onDidThing", lambda a: a, owner="p")
    reg.add("onDidThing", lambda a: -a, owner="q", tryfirst=True)
    outcome = await reg.caller("onDidThing").call(a=5, b=0)
    assert outcome.results == [-5, 5]
    assert reg.block("q") == 1 and reg.names() == ("onDidThing",) and "onDidThing" in reg
    assert reg.specs()["onDidThing"] is SPEC


def test_adding_the_same_implementation_twice_is_one_implementation():
    """Import-time registration (module/contributions.register_all) and the manifest load name the same function."""
    from narranexus.kernel.plugins.hooks import HookRegistry, HookSpec

    reg = HookRegistry()
    reg.declare(HookSpec("onDidThing", ("x",)))
    calls: list[int] = []

    def impl(x):
        calls.append(x)

    reg.add("onDidThing", impl, owner="acme.a")
    reg.add("onDidThing", impl, owner="acme.a")
    assert len(reg.caller("onDidThing")) == 1
    reg.add("onDidThing", impl, owner="acme.b")  # another owner registering the same fn is a second impl
    assert len(reg.caller("onDidThing")) == 2
