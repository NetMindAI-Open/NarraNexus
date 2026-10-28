"""
@file_name: test_builtin_trigger_specs.py
@author: Bin Liang
@date: 2026-09-07
@description: Every builtin TriggerSpec's host and kwargs are pinned, not just its name.

The two golden files record trigger NAMES and class names only, so the values
that actually decide runtime behaviour were unpinned across all 21 contribution
tables: changing ``builtin.job``'s ``poll_interval`` from 60 to 6000, or moving
it from the workers supervisor to ``host="api"``, was green everywhere. The job
clock is the load-bearing case — ``poll_interval`` is how often every scheduled
job in the product is even looked at, and ``max_workers`` caps how many run at
once — so its values are asserted literally here, and every other builtin
trigger has its ``host`` pinned against the process that is supposed to run it.
"""
from __future__ import annotations

import pytest

from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import Registries


@pytest.fixture(scope="module")
def triggers() -> dict[str, object]:
    regs = Registries()
    load_builtins(regs, "workers")
    return {e.name: e.factory() for e in regs.registry_for("ingress.triggers").entries()}


def test_the_job_clock_keeps_its_schedule_and_its_worker_cap(triggers):
    spec = triggers["jobs"]
    assert spec.host == "workers"  # run.sh / compose address it with --only jobs
    assert spec.class_ref == "narranexus_plugins.job_module.job_trigger:JobTrigger"
    assert dict(spec.kwargs) == {"poll_interval": 60, "max_workers": 5}


def test_every_builtin_trigger_runs_on_the_process_that_supervises_it(triggers):
    """A channel trigger on ``host="workers"`` (or the reverse) silently moves a
    long-lived connection into the wrong supervisor: it is started by a process
    that does not restart it and is missing from the one that does."""
    assert len(triggers) >= 7, sorted(triggers)
    hosts = {name: spec.host for name, spec in triggers.items()}
    assert hosts["jobs"] == "workers"
    channels = {"lark", "slack", "telegram", "wechat", "narramessenger", "discord"}
    assert channels <= set(hosts), sorted(channels - set(hosts))
    assert {hosts[name] for name in channels} == {"channels"}
