"""
@file_name: test_npm_prefix_installer.py
@author: NarraNexus
@date: 2026-08-28
@description: Tests for NpmPrefixInstaller — ``npm install --prefix <nodejs>``
              driving the managed Claude CLI. No real npm process runs.
"""
from __future__ import annotations

import subprocess

import pytest

from backend.integrations.plugins._installers.npm_prefix import NpmPrefixInstaller
from backend.integrations.plugins.spec import InstallComponent


class _FakeStdout:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProcess:
    def __init__(self, lines: list[bytes], returncode: int = 0):
        self.stdout = _FakeStdout(lines)
        self._returncode = returncode
        # Mirrors real asyncio.subprocess.Process: None until wait()/exit is
        # observed. base.py's cleanup `finally` reads this to decide whether
        # to kill+reap, so it must reflect "already exited" once wait() runs.
        self.returncode: int | None = None

    async def wait(self):
        self.returncode = self._returncode
        return self._returncode


@pytest.fixture()
def plugin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_install_invokes_npm_with_prefix_and_pinned_requirement(plugin_home, monkeypatch):
    from narranexus.platform.agent_framework.plugin_paths import node_prefix

    captured_cmd: list[str] = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _FakeProcess([b"added 1 package\n"])

    monkeypatch.setattr(
        "backend.integrations.plugins._installers.base.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    installer = NpmPrefixInstaller()
    component = InstallComponent(kind="npm", requirement="@anthropic-ai/claude-code@2.1.220")
    target = node_prefix()
    lines = [line async for line in installer.install(component, target)]

    assert lines == ["added 1 package"]
    assert captured_cmd[0] == "npm"
    assert "install" in captured_cmd
    assert "--prefix" in captured_cmd
    prefix_index = captured_cmd.index("--prefix")
    assert captured_cmd[prefix_index + 1] == str(target)
    assert captured_cmd[-1] == "@anthropic-ai/claude-code@2.1.220"


def test_detect_reports_not_installed_when_binary_absent(plugin_home):
    from narranexus.platform.agent_framework.plugin_paths import node_prefix

    installer = NpmPrefixInstaller()
    component = InstallComponent(kind="npm", requirement="@anthropic-ai/claude-code@2.1.220")
    state = installer.detect(component, node_prefix())
    assert state.installed is False
    assert state.version is None
    assert state.target_version == "2.1.220"


def test_detect_reports_update_available_when_cli_reports_older_version(plugin_home, monkeypatch):
    from narranexus.platform.agent_framework.plugin_paths import claude_cli_path, node_prefix

    cli_path = claude_cli_path()
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("#!/bin/sh\necho fake claude\n")
    cli_path.chmod(0o755)

    fake_run_result = subprocess.CompletedProcess(args=[str(cli_path), "--version"], returncode=0, stdout="2.1.56 (Claude Code)\n", stderr="")
    monkeypatch.setattr(
        "backend.integrations.plugins._installers.npm_prefix.subprocess.run",
        lambda *a, **k: fake_run_result,
    )

    installer = NpmPrefixInstaller()
    component = InstallComponent(kind="npm", requirement="@anthropic-ai/claude-code@2.1.220")
    state = installer.detect(component, node_prefix())

    assert state.installed is True
    assert state.version == "2.1.56"
    assert state.target_version == "2.1.220"
    assert state.update_available is True


def test_detect_reports_no_update_when_cli_version_matches_pin(plugin_home, monkeypatch):
    from narranexus.platform.agent_framework.plugin_paths import claude_cli_path, node_prefix

    cli_path = claude_cli_path()
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("#!/bin/sh\necho fake claude\n")
    cli_path.chmod(0o755)

    fake_run_result = subprocess.CompletedProcess(args=[str(cli_path), "--version"], returncode=0, stdout="2.1.220 (Claude Code)\n", stderr="")
    monkeypatch.setattr(
        "backend.integrations.plugins._installers.npm_prefix.subprocess.run",
        lambda *a, **k: fake_run_result,
    )

    installer = NpmPrefixInstaller()
    component = InstallComponent(kind="npm", requirement="@anthropic-ai/claude-code@2.1.220")
    state = installer.detect(component, node_prefix())

    assert state.installed is True
    assert state.version == "2.1.220"
    assert state.update_available is False


def test_detect_reports_installed_with_unknown_version_when_probe_output_unreadable(plugin_home, monkeypatch):
    """CLI binary is present but `--version` output doesn't contain a
    parseable version — must still report installed=True (a present-but-
    unreadable binary is not "not installed"), with version=None. Revert to
    the old "installed follows version-readable" semantics and this goes
    red."""
    from narranexus.platform.agent_framework.plugin_paths import claude_cli_path, node_prefix

    cli_path = claude_cli_path()
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("#!/bin/sh\necho fake claude\n")
    cli_path.chmod(0o755)

    fake_run_result = subprocess.CompletedProcess(
        args=[str(cli_path), "--version"], returncode=0, stdout="unreadable output\n", stderr=""
    )
    monkeypatch.setattr(
        "backend.integrations.plugins._installers.npm_prefix.subprocess.run",
        lambda *a, **k: fake_run_result,
    )

    installer = NpmPrefixInstaller()
    component = InstallComponent(kind="npm", requirement="@anthropic-ai/claude-code@2.1.220")
    state = installer.detect(component, node_prefix())

    assert state.installed is True
    assert state.version is None
    assert state.target_version == "2.1.220"
    assert state.update_available is False


@pytest.mark.asyncio
async def test_uninstall_removes_only_the_package_not_the_whole_prefix(plugin_home, monkeypatch):
    """Uninstall must `npm uninstall <pkg> --prefix`, NOT wipe the shared
    node_prefix tree — otherwise a future second npm plugin in the same prefix
    would be collateral damage. Delete the precise-uninstall change and this
    goes red (it would rmtree instead of shelling npm)."""
    from narranexus.platform.agent_framework.plugin_paths import node_prefix

    captured_cmd: list[str] = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _FakeProcess([b"removed 1 package\n"])

    monkeypatch.setattr(
        "backend.integrations.plugins._installers.base.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    installer = NpmPrefixInstaller()
    component = InstallComponent(kind="npm", requirement="@anthropic-ai/claude-code@2.1.220")
    await installer.uninstall(component, node_prefix())

    assert captured_cmd[0] == "npm"
    assert "uninstall" in captured_cmd
    assert "--prefix" in captured_cmd
    assert captured_cmd[captured_cmd.index("--prefix") + 1] == str(node_prefix())
    # The bare package name (version stripped), never the whole prefix path.
    assert captured_cmd[-1] == "@anthropic-ai/claude-code"
