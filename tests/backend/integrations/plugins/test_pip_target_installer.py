"""
@file_name: test_pip_target_installer.py
@author: NarraNexus
@date: 2026-08-28
@description: Tests for PipTargetInstaller — the ``sys.executable -m pip
              install --target <pyenv>`` strategy. Uses a fake subprocess so
              no real network/pip call happens.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.integrations.plugins._installers.base import PluginInstallSubprocessError
from backend.integrations.plugins._installers.pip_target import PipTargetInstaller
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


def _write_fake_dist(pyenv_dir: Path, import_name: str, dist_prefix: str, version: str) -> None:
    (pyenv_dir / import_name).mkdir(parents=True, exist_ok=True)
    dist_info = pyenv_dir / f"{dist_prefix}-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist_prefix}\nVersion: {version}\n")


@pytest.mark.asyncio
async def test_install_invokes_pip_with_target_and_pinned_requirement(plugin_home, monkeypatch):
    from backend.integrations.plugins import _installers as installers_pkg  # noqa: F401
    from narranexus.platform.agent_framework.plugin_paths import pyenv_dir

    captured_cmd: list[str] = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _FakeProcess([b"Collecting claude-agent-sdk==0.1.43\n", b"Successfully installed\n"])

    monkeypatch.setattr(
        "backend.integrations.plugins._installers.base.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    # Pin the pip-present branch so this test is deterministic regardless of
    # whether the running interpreter happens to have pip.
    monkeypatch.setattr(
        "backend.integrations.plugins._installers.pip_target.importlib.util.find_spec",
        lambda name: object(),
    )

    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    target = pyenv_dir() / "claude_code"
    lines = [line async for line in installer.install(component, target)]

    assert lines == ["Collecting claude-agent-sdk==0.1.43", "Successfully installed"]
    assert captured_cmd[0] == sys.executable
    assert "-m" in captured_cmd and "pip" in captured_cmd
    assert "ensurepip" not in captured_cmd  # pip present → no bootstrap
    assert "--target" in captured_cmd
    target_index = captured_cmd.index("--target")
    assert captured_cmd[target_index + 1] == str(target)
    assert captured_cmd[-1] == "claude-agent-sdk==0.1.43"


@pytest.mark.asyncio
async def test_install_bootstraps_pip_via_ensurepip_when_absent(plugin_home, monkeypatch):
    """A uv-managed venv (bash run.sh) ships no pip — install must ensurepip
    first, then run pip. Delete the bootstrap branch and this goes red."""
    commands: list[list[str]] = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        commands.append(list(cmd))
        return _FakeProcess([b"ok\n"])

    monkeypatch.setattr(
        "backend.integrations.plugins._installers.base.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    # Force the "pip missing" branch.
    monkeypatch.setattr(
        "backend.integrations.plugins._installers.pip_target.importlib.util.find_spec",
        lambda name: None,
    )

    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="openai-codex==0.1.0b3")
    target = plugin_home / "pyenv" / "codex_cli"
    _ = [line async for line in installer.install(component, target)]

    assert len(commands) == 2
    assert commands[0] == [sys.executable, "-m", "ensurepip", "--default-pip"]
    assert commands[1][:3] == [sys.executable, "-m", "pip"]
    assert commands[1][-1] == "openai-codex==0.1.0b3"


@pytest.mark.asyncio
async def test_install_raises_on_nonzero_exit(plugin_home, monkeypatch):
    async def fake_create_subprocess_exec(*cmd, **kwargs):
        return _FakeProcess([b"ERROR: could not find a version\n"], returncode=1)

    monkeypatch.setattr(
        "backend.integrations.plugins._installers.base.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    target = plugin_home / "pyenv" / "claude_code"

    with pytest.raises(PluginInstallSubprocessError):
        _ = [line async for line in installer.install(component, target)]


def test_detect_reports_not_installed_when_dir_absent(plugin_home):
    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    target = plugin_home / "pyenv" / "claude_code"
    state = installer.detect(component, target)
    assert state.installed is False
    assert state.version is None
    assert state.target_version == "0.1.43"
    assert state.update_available is False


def test_detect_reports_update_available_when_version_below_pin(plugin_home):
    target = plugin_home / "pyenv" / "claude_code"
    _write_fake_dist(target, "claude_agent_sdk", "claude_agent_sdk", "0.1.40")
    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    state = installer.detect(component, target)
    assert state.installed is True
    assert state.version == "0.1.40"
    assert state.target_version == "0.1.43"
    assert state.update_available is True


def test_detect_reports_no_update_when_version_matches_pin(plugin_home):
    target = plugin_home / "pyenv" / "claude_code"
    _write_fake_dist(target, "claude_agent_sdk", "claude_agent_sdk", "0.1.43")
    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    state = installer.detect(component, target)
    assert state.installed is True
    assert state.version == "0.1.43"
    assert state.update_available is False


@pytest.mark.asyncio
async def test_uninstall_removes_the_entire_target_directory(plugin_home):
    """Uninstall rmtree's the whole per-plugin pyenv subdir — not just the
    pinned package's own dir/dist-info — so a plugin's full dependency
    closure (unrelated helper packages included) goes with it. Delete the
    rmtree call (revert to a package-only glob delete) and this goes red."""
    target = plugin_home / "pyenv" / "claude_code"
    _write_fake_dist(target, "claude_agent_sdk", "claude_agent_sdk", "0.1.43")
    # A sibling dependency that has nothing to do with the pinned package
    # name/glob — proves the whole subdir is taken, not just matched entries.
    (target / "some_dependency").mkdir(parents=True)
    (target / "some_dependency" / "__init__.py").write_text("")

    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")

    assert target.is_dir()
    await installer.uninstall(component, target)

    assert not target.exists()


@pytest.mark.asyncio
async def test_uninstall_of_absent_target_is_a_noop(plugin_home):
    installer = PipTargetInstaller()
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    target = plugin_home / "pyenv" / "claude_code"

    assert not target.exists()
    await installer.uninstall(component, target)  # must not raise

    assert not target.exists()
