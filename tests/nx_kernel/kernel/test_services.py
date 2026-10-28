"""
@file_name: test_services.py
@author: Bin Liang
@date: 2026-09-03
@description: ServiceRef DI: expose/require, conflicts, owner release, and plugin scope exposing only as itself.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import PluginError, RegistryConflict, UnknownEntry
from narranexus.kernel.plugins.services import ServiceLocator, ServiceRef

WEATHER = ServiceRef[object]("acme.weather")


def test_expose_require_conflict_and_release():
    root = ServiceLocator()
    impl = object()
    d = root.expose(WEATHER, impl, owner="acme.weather")
    assert root.require(WEATHER) is impl and root.owner_of(WEATHER) == "acme.weather"
    with pytest.raises(RegistryConflict):
        root.expose(WEATHER, object(), owner="acme.other")
    assert root.release_owner("acme.weather") == 1
    with pytest.raises(UnknownEntry, match="not exposed"):
        root.require(WEATHER)
    assert root.try_require(WEATHER) is None
    d.dispose()  # already gone: no-op


def test_scoped_view_exposes_as_the_plugin_and_frozen_refuses():
    root = ServiceLocator()
    scoped = root.scoped("acme.weather")
    scoped.expose(WEATHER, "facade")
    assert root.owner_of(WEATHER) == "acme.weather"
    assert scoped.require(WEATHER) == "facade"
    root.freeze()
    from narranexus.contracts import PluginError

    with pytest.raises(PluginError, match="locator is frozen"):
        root.expose(ServiceRef("x"), 1, owner="a")


def test_ref_id_validation():
    with pytest.raises(PluginError, match="non-empty token"):
        ServiceRef("bad id")
