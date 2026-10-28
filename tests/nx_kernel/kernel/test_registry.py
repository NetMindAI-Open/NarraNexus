"""
@file_name: test_registry.py
@author: Bin Liang
@date: 2026-09-03
@description: Registry[T] semantics — deterministic order, fail-loud lookup, conflicts, freeze, disposal.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import RegistryConflict, RegistryFrozen, UnknownEntry
from narranexus.kernel.plugins.registry import Contribution, Registry


def test_register_get_names_are_registration_ordered():
    r: Registry[int] = Registry("demo", api_version=0)
    r.register("b", lambda: 2, owner="p1")
    r.register("a", lambda: 1, owner="p2")
    assert r.names() == ("b", "a")
    assert r.get("a") == 1 and r.get("b") == 2
    assert len(r) == 2 and "a" in r and list(r) == ["b", "a"]


def test_duplicate_name_conflicts_unless_replace_and_records_it():
    r: Registry[int] = Registry("demo", api_version=0)
    r.register("a", lambda: 1, owner="p1")
    with pytest.raises(RegistryConflict, match="already provided by 'p1'"):
        r.register("a", lambda: 2, owner="p2")
    r.register("a", lambda: 3, owner="p2", replace=True)
    assert r.get("a") == 3 and r.owner_of("a") == "p2"
    (entry,) = r.entries()
    assert entry.replaced is True


def test_unknown_name_fails_loud_and_try_get_returns_none():
    r: Registry[int] = Registry("demo", api_version=0)
    with pytest.raises(UnknownEntry, match="unknown entry 'nope'"):
        r.get("nope")
    with pytest.raises(KeyError):
        r.get("nope")
    with pytest.raises(UnknownEntry):
        r.owner_of("nope")
    assert r.try_get("nope") is None


def test_dispose_unregisters_before_freeze_and_is_ignored_after():
    r: Registry[int] = Registry("demo", api_version=0)
    d = r.register("a", lambda: 1, owner="p1")
    d.dispose()
    assert r.try_get("a") is None
    d2 = r.register("b", lambda: 2, owner="p1")
    r.freeze()
    assert r.frozen
    d2.dispose()
    assert r.get("b") == 2
    with pytest.raises(RegistryFrozen):
        r.register("c", lambda: 3, owner="p1")


def test_dispose_does_not_remove_a_replacement_registered_under_the_same_name():
    r: Registry[int] = Registry("demo", api_version=0)
    first = r.register("a", lambda: 1, owner="p1")
    r.register("a", lambda: 2, owner="p2", replace=True)
    first.dispose()
    assert r.get("a") == 2


def test_factory_is_lazy_and_called_on_every_get():
    calls: list[int] = []
    r: Registry[int] = Registry("demo", api_version=0)
    r.register("a", lambda: calls.append(1) or 1, owner="p")
    assert calls == []
    r.get("a")
    r.get("a")
    assert calls == [1, 1]


def test_normalize_makes_keys_case_insensitive():
    r: Registry[int] = Registry("demo", api_version=0, normalize=lambda s: s.strip().lower())
    r.register(" Claude ", lambda: 1, owner="p")
    assert r.get("claude") == 1 and r.names() == ("claude",) and "CLAUDE" in r


def test_entries_expose_owner_and_meta_copy():
    r: Registry[int] = Registry("demo", api_version=0)
    meta = {"x": 1}
    r.register("a", lambda: 1, owner="p", meta=meta)
    meta["x"] = 2
    (e,) = r.entries()
    assert (e.name, e.owner, e.meta) == ("a", "p", {"x": 1})


def test_registering_the_same_factory_object_twice_is_idempotent():
    r: Registry[int] = Registry("demo", api_version=0)
    factory = lambda: 1  # noqa: E731 — identity matters here
    r.register("a", factory, owner="p1")
    r.register("a", factory, owner="p2")  # no conflict, still one entry
    assert len(r) == 1 and r.owner_of("a") == "p1"
    with pytest.raises(RegistryConflict):
        r.register("a", lambda: 1, owner="p3")


def test_register_contribution_carries_name_factory_and_meta():
    r: Registry[int] = Registry("demo", api_version=0)
    c = Contribution("x", lambda: 7, meta={"display": "X"})
    r.register_contribution(c, owner="p")
    r.register_contribution(c, owner="p")
    (e,) = r.entries()
    assert (e.name, r.get("x"), e.meta) == ("x", 7, {"display": "X"})
