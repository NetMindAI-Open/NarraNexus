"""
@file_name: test_bisect.py
@author: Bin Liang
@date: 2026-09-03
@description: Bisect halves the suspect set each answer, converges in O(log N) steps, and leaves only the culprit disabled.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from narranexus.kernel.plugins.bisect import Bisect
from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryError, RegistryStore


def _store(tmp_path: Path, n: int) -> RegistryStore:
    store = RegistryStore(path=tmp_path / "registry.json", lkg=tmp_path / "lkg.json")
    for i in range(n):
        store.register(f"acme.p{i:02d}", PluginRecord(path=f"/p{i}"))
    return store


@pytest.mark.parametrize("n,culprit", [(2, 0), (5, 3), (8, 7), (9, 4)])
def test_converges_in_log_steps(tmp_path: Path, n: int, culprit: int):
    store = _store(tmp_path, n)
    bad = f"acme.p{culprit:02d}"
    bisect = Bisect(store)
    step = bisect.start()
    answers = 0
    while step.culprit is None:
        good = bad not in step.trial
        step = bisect.answer(good=good)
        answers += 1
    assert step.culprit == bad and answers <= math.ceil(math.log2(n))
    bisect.stop()
    reg = store.read()
    assert not reg.plugins[bad].enabled and "bisect" in reg.plugins[bad].warnings[0]
    assert all(rec.enabled for pid, rec in reg.plugins.items() if pid != bad)
    assert reg.bisect is None


def test_needs_two_plugins_and_no_answer_without_start(tmp_path: Path):
    store = _store(tmp_path, 1)
    with pytest.raises(RegistryError, match="at least two"):
        Bisect(store).start()
    with pytest.raises(RegistryError, match="no bisect"):
        Bisect(_store(tmp_path / "b", 3)).answer(good=True)


def test_trial_half_is_persisted_as_enabled_flags(tmp_path: Path):
    store = _store(tmp_path, 4)
    step = Bisect(store).start()
    reg = store.read()
    assert set(step.trial) == {pid for pid, rec in reg.plugins.items() if rec.enabled}
    assert len(step.trial) == 2 and reg.bisect is not None
