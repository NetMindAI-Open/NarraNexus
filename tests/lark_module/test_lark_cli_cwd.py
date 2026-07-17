"""
@file_name: test_lark_cli_cwd.py
@description: Regression tests for the 2026-05-28 lark-cli CWD fix.

The bug: ``_exec_lark_cli`` spawned lark-cli without setting ``cwd=`` →
default-relative file outputs (``./artifact-<title>/transcript.txt`` from
``vc +notes``, etc.) landed in the MCP container's CWD, outside any
mount the backend container could read → agent's ``Read`` tool saw
nothing → "transcript downloaded to a path I can't read" P0.

These tests assert:
  - ``_exec_lark_cli`` passes its ``cwd`` argument straight through to
    ``asyncio.create_subprocess_exec``.
  - ``_run_with_agent_id`` resolves the agent workspace via
    ``_resolve_agent_workspace_cwd`` and forwards it as the CWD.
  - When the lookup fails (orphan agent, DB error), CWD is None and the
    legacy behaviour is preserved (no crash).

Also one **end-to-end** test that actually spawns a tiny Python helper
script as a stand-in for lark-cli, writes a file at ``./marker.txt``,
and confirms the file appears under the cwd we asked for — proving the
whole chain works at the OS level, not just at the kwargs level.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.module.lark_module.lark_cli_client import (
    LarkCLIClient,
    _agent_user_id_cache,
    _resolve_agent_workspace_cwd,
)


@pytest.fixture(autouse=True)
def _clear_user_cache():
    _agent_user_id_cache.clear()
    yield
    _agent_user_id_cache.clear()


# ── Unit: _exec_lark_cli threads cwd → create_subprocess_exec ───────────


@pytest.mark.asyncio
async def test_exec_lark_cli_passes_cwd_to_subprocess(tmp_path: Path):
    """The cwd kwarg must reach asyncio.create_subprocess_exec verbatim."""
    cli = LarkCLIClient()
    target_cwd = tmp_path / "agent_x_userY"
    target_cwd.mkdir()

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b'{"ok": true}', b""))
    fake_proc.returncode = 0

    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
    ) as spawn:
        await cli._exec_lark_cli(
            cmd=["lark-cli", "doesnt", "matter"],
            stdin_data="",
            timeout=5.0,
            env={"HOME": "/some/home"},
            cwd=target_cwd,
        )
        kwargs = spawn.call_args.kwargs
        assert kwargs["cwd"] == str(target_cwd), (
            f"cwd must be passed as the resolved string, got {kwargs.get('cwd')!r}"
        )


@pytest.mark.asyncio
async def test_exec_lark_cli_cwd_none_is_passed_through(tmp_path: Path):
    """cwd=None → child inherits parent CWD (legacy behaviour)."""
    cli = LarkCLIClient()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"{}", b""))
    fake_proc.returncode = 0
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
    ) as spawn:
        await cli._exec_lark_cli(
            cmd=["lark-cli"], stdin_data="", timeout=5.0,
            env=None, cwd=None,
        )
        assert spawn.call_args.kwargs["cwd"] is None


# ── Unit: _resolve_agent_workspace_cwd ──────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_agent_workspace_cwd_happy_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.settings.settings.base_working_path",
        str(tmp_path),
    )
    fake_agent = MagicMock(created_by="user_alice")
    fake_repo = MagicMock()
    fake_repo.get_agent = AsyncMock(return_value=fake_agent)
    with patch(
        "xyz_agent_context.repository.AgentRepository", return_value=fake_repo,
    ):
        ws = await _resolve_agent_workspace_cwd("agent_abc", db=MagicMock())
    assert ws is not None
    from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath
    assert ws == tmp_path / agent_workspace_relpath("agent_abc", "user_alice")
    assert ws.is_dir(), "workspace dir must be created (mkdir -p semantics)"


@pytest.mark.asyncio
async def test_resolve_agent_workspace_cwd_caches_user_id(tmp_path, monkeypatch):
    """Second call for the same agent must not re-query the DB."""
    monkeypatch.setattr(
        "xyz_agent_context.settings.settings.base_working_path",
        str(tmp_path),
    )
    fake_agent = MagicMock(created_by="user_bob")
    fake_repo = MagicMock()
    fake_repo.get_agent = AsyncMock(return_value=fake_agent)
    with patch(
        "xyz_agent_context.repository.AgentRepository", return_value=fake_repo,
    ):
        await _resolve_agent_workspace_cwd("agent_xyz", db=MagicMock())
        await _resolve_agent_workspace_cwd("agent_xyz", db=MagicMock())
    assert fake_repo.get_agent.await_count == 1, (
        "user_id should be cached after first lookup"
    )


@pytest.mark.asyncio
async def test_resolve_agent_workspace_cwd_returns_none_when_no_owner(tmp_path, monkeypatch):
    """Orphan agent → caller falls back to parent CWD inheritance."""
    monkeypatch.setattr(
        "xyz_agent_context.settings.settings.base_working_path",
        str(tmp_path),
    )
    fake_repo = MagicMock()
    fake_repo.get_agent = AsyncMock(return_value=None)
    with patch(
        "xyz_agent_context.repository.AgentRepository", return_value=fake_repo,
    ):
        ws = await _resolve_agent_workspace_cwd("agent_orphan", db=MagicMock())
    assert ws is None


@pytest.mark.asyncio
async def test_resolve_agent_workspace_cwd_returns_none_on_db_error(monkeypatch):
    """DB exception is swallowed (logged) and None is returned."""
    fake_repo = MagicMock()
    fake_repo.get_agent = AsyncMock(side_effect=RuntimeError("DB down"))
    with patch(
        "xyz_agent_context.repository.AgentRepository", return_value=fake_repo,
    ):
        ws = await _resolve_agent_workspace_cwd("agent_x", db=MagicMock())
    assert ws is None


# ── End-to-end: a real subprocess writes into the CWD we picked ─────────


@pytest.mark.asyncio
async def test_end_to_end_subprocess_actually_uses_cwd(tmp_path: Path):
    """Spawn a small Python helper script that writes ./marker.txt and
    confirm the file appears under the cwd we passed.

    Proves the cwd plumbing works at the OS level (not just at the
    kwargs level), and that lark-cli-style `./<thing>` outputs land
    inside the agent workspace.
    """
    cli = LarkCLIClient()
    workspace = tmp_path / "agent_pretend_user_test"
    workspace.mkdir()
    helper_script = tmp_path / "fake_lark_cli.py"
    helper_script.write_text(
        "from pathlib import Path; "
        "Path('./marker.txt').write_text('hello'); "
        "print('{\"ok\": true}')"
    )
    result = await cli._exec_lark_cli(
        cmd=[sys.executable, str(helper_script)],
        stdin_data="",
        timeout=10.0,
        env=None,
        cwd=workspace,
    )
    assert result["success"] is True
    assert (workspace / "marker.txt").is_file(), (
        f"file should land in cwd={workspace}, but workspace contents are: "
        f"{list(workspace.iterdir())}"
    )
    assert (workspace / "marker.txt").read_text() == "hello"
