"""
@file_name: test_plugin_paths.py
@author: NarraNexus
@date: 2026-08-28
@description: Contract tests for plugin_paths (WHERE optional framework plugins install and the
              package probe) and the registry-driven framework_installed built on it.

Regression guard: delete the target logic and one of these must go red.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from narranexus.platform.agent_framework import plugin_paths as pp
from narranexus.platform.agent_framework.loop.driver import framework_installed


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Point the plugin home at an empty temp dir via the env override."""
    home = tmp_path / "plugins"
    monkeypatch.setenv(pp.ENV_PLUGIN_HOME, str(home))
    return home


def test_env_override_wins(isolated_home):
    assert pp.plugin_home() == isolated_home


def test_default_home_when_env_unset(monkeypatch):
    monkeypatch.delenv(pp.ENV_PLUGIN_HOME, raising=False)
    assert pp.plugin_home() == Path.home() / ".narranexus" / "plugins"


def test_path_composition(isolated_home):
    assert pp.node_prefix() == isolated_home / "nodejs"
    assert pp.pyenv_dir() == isolated_home / "pyenv"
    assert pp.claude_cli_path() == (
        isolated_home / "nodejs" / "node_modules" / ".bin" / "claude"
    )


def test_nexus_power_always_installed(isolated_home):
    # No filesystem, no import — a framework without an install recipe is
    # available by virtue of being registered.
    assert framework_installed("nexus_power") is True


def test_unknown_framework_never_installed(isolated_home):
    assert framework_installed("does_not_exist") is False


def test_claude_absent_when_neither_pyenv_nor_base(isolated_home, monkeypatch):
    # Force the base-env probe to miss so only the pyenv filesystem matters.
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: False)
    assert framework_installed("claude_code") is False
    assert framework_installed("codex_cli") is False


def test_plugin_pyenv_is_a_per_plugin_subdir(isolated_home):
    assert pp.plugin_pyenv("claude_code") == isolated_home / "pyenv" / "claude_code"
    assert pp.plugin_pyenv("codex_cli") == isolated_home / "pyenv" / "codex_cli"


def test_claude_present_via_pyenv(isolated_home, monkeypatch):
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: False)
    # Package lives in the plugin's OWN subdir (pyenv/<plugin_id>/<package>).
    (isolated_home / "pyenv" / "claude_code" / "claude_agent_sdk").mkdir(parents=True)
    assert framework_installed("claude_code") is True
    assert framework_installed("codex_cli") is False  # only claude's subdir exists


def test_codex_present_via_pyenv(isolated_home, monkeypatch):
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: False)
    (isolated_home / "pyenv" / "codex_cli" / "openai_codex").mkdir(parents=True)
    assert framework_installed("codex_cli") is True


def test_claude_pkg_in_wrong_subdir_is_not_installed(isolated_home, monkeypatch):
    # A package under the WRONG plugin's subdir must not count — availability is
    # keyed to pyenv/<plugin_id>/<package>, not pyenv/**/<package>.
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: False)
    (isolated_home / "pyenv" / "codex_cli" / "claude_agent_sdk").mkdir(parents=True)
    assert framework_installed("claude_code") is False


def test_present_via_base_env(isolated_home, monkeypatch):
    # Cloud / normal install: package sits in the ordinary site-packages,
    # not the plugin pyenv. Availability must still report True.
    monkeypatch.setattr(pp, "_present_in_base", lambda pkg: pkg == "claude_agent_sdk")
    assert framework_installed("claude_code") is True
    assert framework_installed("codex_cli") is False


def test_activate_pyenv_appends_each_plugin_subdir(isolated_home):
    (isolated_home / "pyenv" / "claude_code").mkdir(parents=True)
    (isolated_home / "pyenv" / "codex_cli").mkdir(parents=True)
    claude_dir = str(isolated_home / "pyenv" / "claude_code")
    codex_dir = str(isolated_home / "pyenv" / "codex_cli")
    try:
        pp.activate_pyenv()
        # Appended in sorted order at the very END, so base packages still win
        # for shared deps (the whole reason activate_pyenv appends, not inserts).
        assert sys.path[-2:] == [claude_dir, codex_dir]
        # Idempotent.
        pp.activate_pyenv()
        assert sys.path.count(claude_dir) == 1
        assert sys.path.count(codex_dir) == 1
    finally:
        for entry in (claude_dir, codex_dir):
            while entry in sys.path:
                sys.path.remove(entry)


def test_activate_pyenv_noop_when_absent(isolated_home):
    before = list(sys.path)
    pp.activate_pyenv()
    assert sys.path == before  # nothing installed → no subdirs appended
