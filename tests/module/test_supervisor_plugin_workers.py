"""
@file_name: test_supervisor_plugin_workers.py
@author: Bin Liang
@date: 2026-09-03
@description: backend.workers contributions with host="workers" join the supervisor after the builtin four, namespaced <owner>:<name>.
"""
from __future__ import annotations

import asyncio

from narranexus.contracts.worker import WorkerSpec as ContractWorkerSpec
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.module_system import run_worker_supervisor as sup


def _registries(*specs):
    # Builtin triggers (the "jobs" worker) are registry contributions too, so a
    # realistic process registry carries them before any plugin worker.
    from narranexus.kernel.plugins.builtins import load_builtins

    registries = Registries()
    load_builtins(registries, "backend")
    reg = registries.registry_for("backend.workers")
    for owner, name, spec in specs:
        reg.register_contribution(Contribution(name, (lambda s=spec: s)), owner=owner)
    return registries


class _Handle:
    def __init__(self):
        self.stopped = False
        self.run = self._run()

    async def _run(self):
        await asyncio.sleep(0)

    def stop(self):
        self.stopped = True


def test_plugin_workers_follow_builtins_and_are_namespaced():
    async def factory(ctx):
        return _Handle()

    registries = _registries(
        ("acme.weather", "sync", ContractWorkerSpec("sync", factory, stable_after_s=5)),
        ("acme.weather", "api", ContractWorkerSpec("api", factory, host="backend")),
    )
    specs = sup.build_specs(registries=registries)
    assert [s.name for s in specs] == list(sup.ALL_WORKERS) + ["acme.weather:sync"]
    assert specs[-1].stable_after_s == 5.0


def test_only_and_exclude_address_plugin_workers_by_full_name():
    async def factory(ctx):
        return _Handle()

    registries = _registries(("acme.weather", "sync", ContractWorkerSpec("sync", factory)))
    assert [s.name for s in sup.build_specs(only={"acme.weather:sync"}, registries=registries)] == ["acme.weather:sync"]
    names = [s.name for s in sup.build_specs(exclude={"acme.weather:sync"}, registries=registries)]
    assert names == list(sup.ALL_WORKERS)


def test_adapted_factory_builds_a_supervisor_handle_and_broken_spec_is_skipped():
    handle = _Handle()

    async def factory(ctx):
        return handle

    def boom():
        raise RuntimeError("bad spec")

    registries = _registries(("acme.weather", "sync", ContractWorkerSpec("sync", factory)))
    registries.registry_for("backend.workers").register_contribution(Contribution("broken", boom), owner="acme.x")
    specs = sup.build_specs(registries=registries)
    assert [s.name for s in specs][-1] == "acme.weather:sync"
    built = asyncio.run(specs[-1].factory(None))
    assert isinstance(built, sup.WorkerHandle)
    built.stop()
    assert handle.stopped is True
    asyncio.run(built.run)


def test_without_plugins_the_spec_list_is_unchanged():
    assert [s.name for s in sup.build_specs(registries=_registries())] == list(sup.ALL_WORKERS)
