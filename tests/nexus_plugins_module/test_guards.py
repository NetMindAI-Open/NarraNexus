"""
@file_name: test_guards.py
@author: Bin Liang
@date: 2026-09-03
@description: Every guardrail refuses what it must: ids, kinds, paths, extensions, symlink escapes, budgets, rollback counter.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl import guards as g


def test_ids_and_kinds():
    assert g.check_plugin_id("me.weather") == "me.weather"
    for bad in ("weather", "builtin.x", "builtin.nexus_plugins_module", "Me.Weather", "me/weather"):
        with pytest.raises(g.GuardError):
            g.check_plugin_id(bad)
    with pytest.raises(g.GuardError):
        g.check_not_protected("builtin.chat")
    assert g.check_kinds(["routes"], ["routes", "table"]) == ["routes"]
    with pytest.raises(g.GuardError):
        g.check_kinds(["nope"], ["routes"])
    with pytest.raises(g.GuardError):
        g.check_kinds([], ["routes"])


def test_paths_and_extensions(tmp_path: Path):
    plugin = tmp_path / "plugins" / "me.weather"
    plugin.mkdir(parents=True)
    assert g.check_edit_path(plugin, "backend/__init__.py", "x") == (plugin / "backend" / "__init__.py").resolve()
    for bad in ("../x.py", "/abs.py", "backend/x.exe", "pyenv/x.py", ".git/config"):
        with pytest.raises(g.GuardError):
            g.check_edit_path(plugin, bad, "x")
    with pytest.raises(g.GuardError, match="exceeds"):
        g.check_edit_path(plugin, "big.py", "x" * (g.MAX_EDIT_BYTES + 1))
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, plugin / "link")
    with pytest.raises(g.GuardError, match="escapes|outside"):
        g.check_edit_path(plugin, "link/evil.py", "x")
    with pytest.raises(g.GuardError, match="outside"):
        g.check_inside(plugin, tmp_path / "elsewhere")


def test_budget_and_cooldown():
    b = g.Budget(events=[])
    for i in range(g.REGISTER_BUDGET_PER_WINDOW):
        b.check(now=1000 + i)
        b.spend(now=1000 + i)
    with pytest.raises(g.GuardError, match="budget"):
        b.check(now=1010)
    b.check(now=1000 + g.BUDGET_WINDOW_S + 5)  # window passed
    b.rollback()
    assert not b.manual_required
    b.rollback()
    assert b.manual_required
    with pytest.raises(g.GuardError, match="human"):
        b.check(now=99999)
    b.success()
    assert b.consecutive_rollbacks == 0
