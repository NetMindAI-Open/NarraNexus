"""
@file_name: test_activation.py
@author: Bin Liang
@date: 2026-09-03
@description: Activation events import + activate once, isolate failures into the crash sink, and deactivate unwinds the context.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from narranexus.contracts import PluginError
from narranexus.contracts.settings import SettingsSchema
from narranexus.kernel.events.bus import EventBus
from narranexus.kernel.plugins import importer
from narranexus.kernel.plugins.activation import Activator, validate_event_name
from narranexus.kernel.plugins.context import build_context
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.services import ServiceLocator
from narranexus.kernel.settings import PluginSettings


def _manifest(registries, pid, events=(), activate=True):
    return parse_manifest(
        {
            "id": pid,
            "version": "1.0.0",
            "displayName": pid,
            "hosts": ["backend"],
            "backend": {"activate": activate},
            "activationEvents": list(events),
        },
        tree=registries.slots,
    )


def _plugin(tmp_path: Path, pid: str, body: str) -> Path:
    backend = tmp_path / pid / "backend"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text(body)
    importer.install_synthetic_package(pid, backend)
    return backend


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for pid in ("acme.a", "acme.b", "acme.bad"):
        importer.uninstall_synthetic_package(pid)


def _activator(registries, crashes):
    def factory(manifest):
        return build_context(
            plugin_id=manifest.id, version=manifest.version, path=Path("/p"), role="backend", host_version="1",
            registries=registries, provides=tuple(manifest.provides), settings=PluginSettings(manifest.id, SettingsSchema(), environ={}),
            db_client=None, bus=EventBus(), services=ServiceLocator().scoped(manifest.id),
        )

    return Activator(context_factory=factory, on_crash=lambda pid, err: crashes.append((pid, err)))


def test_fire_activates_once_and_records_history(tmp_path):
    registries = Registries()
    _plugin(tmp_path, "acme.a", "CALLS = []\n\ndef activate(ctx):\n    CALLS.append(ctx.plugin_id)\n    ctx.subscriptions.add(__import__('narranexus.contracts', fromlist=['Disposable']).Disposable(lambda: CALLS.append('disposed')))\n")
    crashes = []
    act = _activator(registries, crashes)
    assert act.register(_manifest(registries, "acme.a", ["onPage:acme.a", "onEvent:onDidPersistTurn"])) == ("onPage:acme.a", "onEvent:onDidPersistTurn")
    assert act.register(_manifest(registries, "acme.b", activate=False)) == ()
    results = asyncio.run(act.fire("onPage:acme.a"))
    assert [r.plugin_id for r in results] == ["acme.a"] and results[0].error is None
    assert act.is_active("acme.a")
    assert asyncio.run(act.fire("onEvent:onDidPersistTurn")) == []  # already active
    mod = importer.import_plugin_module("acme.a")
    assert mod.CALLS == ["acme.a"]
    assert asyncio.run(act.deactivate("acme.a")) is True and mod.CALLS[-1] == "disposed"
    assert not act.is_active("acme.a") and crashes == []


def test_failed_activation_is_isolated_and_reported(tmp_path):
    registries = Registries()
    _plugin(tmp_path, "acme.bad", "def activate(ctx):\n    raise RuntimeError('nope')\n")
    crashes = []
    act = _activator(registries, crashes)
    act.register(_manifest(registries, "acme.bad", ["onStartup"]))
    (result,) = asyncio.run(act.fire("onStartup"))
    assert result.error == "RuntimeError: nope" and crashes == [("acme.bad", "RuntimeError: nope")]
    assert asyncio.run(act.fire("onStartup")) == []  # failed plugins are not retried by events
    retry = asyncio.run(act.activate_now("acme.bad"))  # explicit retry is allowed
    assert retry.error and len(crashes) == 2


def test_missing_activate_and_bad_event_names(tmp_path):
    registries = Registries()
    _plugin(tmp_path, "acme.a", "X = 1\n")
    act = _activator(registries, [])
    act.register(_manifest(registries, "acme.a", ["onStartup"]))
    (result,) = asyncio.run(act.fire("onStartup"))
    assert "no activate(ctx)" in (result.error or "")
    with pytest.raises(PluginError):
        validate_event_name("onWhatever:x")
    with pytest.raises(PluginError):
        validate_event_name("onPage:")
    with pytest.raises(PluginError):
        asyncio.run(act.activate_now("acme.unknown"))
