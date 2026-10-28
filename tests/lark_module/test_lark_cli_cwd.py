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
    ``resolve_agent_workspace_cwd`` and forwards it as the CWD.
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
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from narranexus.platform.module_system.data_access.workspace_cwd import (
    _cwd_owner_cache,
    resolve_agent_workspace_cwd,
)
from narranexus_plugins.lark_module.lark_cli_client import LarkCLIClient

# The shared owner cache is cleared by the repo-wide autouse fixture in
# tests/conftest.py — every channel CLI test module shares that cache.


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


# ── Unit: resolve_agent_workspace_cwd (shared channel-CLI helper) ───────


def _owner_store(owner):
    """A seam store whose get_agent_owner resolves (or fails) as directed."""
    store = MagicMock()
    if isinstance(owner, Exception):
        store.get_agent_owner = AsyncMock(side_effect=owner)
    else:
        store.get_agent_owner = AsyncMock(return_value=owner)
    return store


@pytest.mark.asyncio
async def test_resolve_agent_workspace_cwd_happy_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "narranexus.platform.settings.settings.base_working_path",
        str(tmp_path),
    )
    with patch(
        "narranexus.platform.module_system.data_access.workspace_cwd.get_channel_credential_store",
        return_value=_owner_store("user_alice"),
    ):
        ws = await resolve_agent_workspace_cwd("agent_abc", log_tag="lark-cli")
    assert ws is not None
    from narranexus.platform.utils.workspace_paths import agent_workspace_relpath
    assert ws == tmp_path / agent_workspace_relpath("agent_abc", "user_alice")
    assert ws.is_dir(), "workspace dir must be created (mkdir -p semantics)"


@pytest.mark.asyncio
async def test_resolve_agent_workspace_cwd_caches_user_id(tmp_path, monkeypatch):
    """Second call for the same agent must not re-query the seam."""
    monkeypatch.setattr(
        "narranexus.platform.settings.settings.base_working_path",
        str(tmp_path),
    )
    store = _owner_store("user_bob")
    with patch(
        "narranexus.platform.module_system.data_access.workspace_cwd.get_channel_credential_store",
        return_value=store,
    ):
        await resolve_agent_workspace_cwd("agent_xyz", log_tag="lark-cli")
        await resolve_agent_workspace_cwd("agent_xyz", log_tag="lark-cli")
    assert store.get_agent_owner.await_count == 1, (
        "user_id should be cached after first lookup"
    )


@pytest.mark.asyncio
async def test_resolve_agent_workspace_cwd_returns_none_when_no_owner(tmp_path, monkeypatch):
    """Orphan agent → caller falls back to parent CWD inheritance, and the
    empty owner must NOT be cached (a later re-bind should re-resolve)."""
    monkeypatch.setattr(
        "narranexus.platform.settings.settings.base_working_path",
        str(tmp_path),
    )
    with patch(
        "narranexus.platform.module_system.data_access.workspace_cwd.get_channel_credential_store",
        return_value=_owner_store(""),
    ):
        ws = await resolve_agent_workspace_cwd("agent_orphan", log_tag="lark-cli")
    assert ws is None
    assert "agent_orphan" not in _cwd_owner_cache


@pytest.mark.asyncio
async def test_resolve_agent_workspace_cwd_returns_none_on_store_error(monkeypatch):
    """Seam exception is swallowed (logged) and None is returned."""
    with patch(
        "narranexus.platform.module_system.data_access.workspace_cwd.get_channel_credential_store",
        return_value=_owner_store(RuntimeError("store down")),
    ):
        ws = await resolve_agent_workspace_cwd("agent_x", log_tag="lark-cli")
    assert ws is None


# ── Regression: _run_with_agent_id after the seam migration ─────────────
#
# The zero-db-creds migration replaced the local ``db``/``mgr`` bindings in
# ``_run_with_agent_id`` with seam reads, but the CWD resolution and the
# workspace_path lazy migration still referenced them — every agent-scoped
# lark-cli call died with ``NameError: name 'db' is not defined`` (prod
# outage 2026-08-14: no agent could reply on Lark). These tests drive
# ``_run_with_agent_id`` end-to-end (subprocess + credential store mocked)
# so any undefined name in its body turns the suite red.


def _fake_cred(workspace_path: str):
    cred = MagicMock()
    cred.workspace_path = workspace_path
    cred.profile_name = "profile-x"
    cred.app_id = "cli_test"
    return cred


@contextmanager
def _seam_env(cred, store, resolver):
    """Patch the seam + hydration + subprocess around _run_with_agent_id."""
    patches = [
        patch(
            "narranexus.platform.module_system.data_access.get_channel_credential_store",
            return_value=store,
        ),
        patch(
            "narranexus_plugins.lark_module._lark_credential_manager._cred_from_raw",
            return_value=cred,
        ),
        patch.object(
            LarkCLIClient, "_ensure_hydrated", new=AsyncMock(return_value=(True, ""))
        ),
        patch.object(
            LarkCLIClient,
            "_exec_lark_cli",
            new=AsyncMock(return_value={"success": True}),
        ),
        patch(
            "narranexus_plugins.lark_module.lark_cli_client."
            "resolve_agent_workspace_cwd",
            new=resolver,
        ),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


def _cred_store():
    store = MagicMock()
    store.get_credential = AsyncMock(return_value={"agent_id": "agent_x"})
    store.patch_credential = AsyncMock(return_value={"success": True})
    return store


@pytest.mark.asyncio
async def test_run_with_agent_id_has_no_undefined_names(tmp_path: Path):
    """Happy path must complete without NameError (the 2026-08-14 outage)."""
    resolver = AsyncMock(return_value=tmp_path)
    with _seam_env(_fake_cred(str(tmp_path)), _cred_store(), resolver):
        result = await LarkCLIClient()._run_with_agent_id(
            ["im", "+messages-send"], "agent_x"
        )
    assert result == {"success": True}
    assert resolver.await_args.args == ("agent_x",)


@pytest.mark.asyncio
async def test_run_with_agent_id_tolerates_unresolved_cwd(tmp_path: Path):
    """Resolver returning None (orphan agent / seam outage) must not block
    the call — it degrades to parent-CWD inheritance."""
    resolver = AsyncMock(return_value=None)
    with _seam_env(_fake_cred(str(tmp_path)), _cred_store(), resolver):
        result = await LarkCLIClient()._run_with_agent_id(["im", "+ping"], "agent_x")
    assert result == {"success": True}


@pytest.mark.asyncio
async def test_run_with_agent_id_lazy_migration_persists_via_seam():
    """workspace_path=='' → path computed and persisted through the seam
    (works in both direct-db and zero-cred deployments)."""
    from narranexus_plugins.lark_module._lark_workspace import (
        get_workspace_path,
    )

    cred = _fake_cred("")
    store = _cred_store()
    with _seam_env(cred, store, AsyncMock(return_value=None)):
        result = await LarkCLIClient()._run_with_agent_id(["im", "+ping"], "agent_x")
    assert result == {"success": True}
    expected_path = str(get_workspace_path("agent_x"))
    assert cred.workspace_path == expected_path
    store.patch_credential.assert_awaited_once_with(
        "lark", "agent_x", {"workspace_path": expected_path}
    )


@pytest.mark.asyncio
async def test_run_with_agent_id_warns_when_migration_write_fails():
    """The seam never raises — a failed lazy-migration write must at least
    leave a warning, or it silently retries on every call forever."""
    cred = _fake_cred("")
    store = _cred_store()
    store.patch_credential = AsyncMock(
        return_value={"success": False, "error": "write_failed"}
    )
    with _seam_env(cred, store, AsyncMock(return_value=None)), patch(
        "narranexus_plugins.lark_module.lark_cli_client.logger"
    ) as log:
        result = await LarkCLIClient()._run_with_agent_id(["im", "+ping"], "agent_x")
    assert result == {"success": True}, "a failed persist must not fail the call"
    assert log.warning.called, "a failed lazy-migration persist must be logged"
    assert "agent_x" in str(log.warning.call_args)


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
