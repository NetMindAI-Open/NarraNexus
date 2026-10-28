"""
@file_name: test_agent_framework_install.py
@date: 2026-05-30 (rewritten 2026-06-08 — npm path removed)
@description: Tests for ``_ensure_codex_installed`` in
``backend/routes/providers.py``.

The codex binary ships with the ``openai-codex-cli-bin`` Python wheel that
``openai-codex`` transitively depends on. The function activates the plugin
pyenv (openai-codex is an OPTIONAL plugin on the lightweight local build —
installed from Settings → Plugins, NOT by run.sh's ``uv sync``), then verifies
the wheel is importable and the binary file exists — no PATH check, no npm
install. Failure guidance points at Settings → Plugins (local) or a cloud
rebuild with ``uv sync --extra plugins``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.routes.providers import _ensure_codex_installed


@pytest.mark.asyncio
async def test_returns_already_installed_when_wheel_binary_present(tmp_path):
    """Happy path: codex_cli_bin imports + the binary file exists."""
    fake_binary = tmp_path / "codex"
    fake_binary.write_text("#!/bin/sh\necho fake codex\n")
    fake_binary.chmod(0o755)
    with patch("codex_cli_bin.bundled_codex_path", return_value=fake_binary):
        r = await _ensure_codex_installed()
    assert r == {"installed": True, "action": "already_installed", "reason": ""}


@pytest.mark.asyncio
async def test_install_failed_when_wheel_missing(monkeypatch):
    """codex_cli_bin not importable → uv sync wasn't run (or failed).
    Report a clear, actionable error."""
    import sys

    # Strip codex_cli_bin from sys.modules so the import inside
    # _ensure_codex_installed gets a fresh attempt; install a finder
    # that raises ImportError for it.
    monkeypatch.delitem(sys.modules, "codex_cli_bin", raising=False)

    class _Blocker:
        def find_module(self, name, path=None):
            return self if name == "codex_cli_bin" else None

        def find_spec(self, name, path, target=None):
            if name == "codex_cli_bin":
                raise ImportError("simulated: codex_cli_bin not installed")
            return None

    monkeypatch.setattr(sys, "meta_path", [_Blocker()] + sys.meta_path)
    r = await _ensure_codex_installed()
    assert r["installed"] is False
    assert r["action"] == "install_failed"
    # Local guidance points at Settings → Plugins (not a bare "uv sync").
    assert "Settings" in r["reason"] and "Plugins" in r["reason"]


@pytest.mark.asyncio
async def test_install_failed_when_binary_file_missing(tmp_path):
    """codex_cli_bin imports OK but the binary file doesn't actually
    exist on disk (corrupted install, partial uv sync, manual delete).
    Surface the absolute path so the user can grep for it."""
    missing_path = tmp_path / "this-does-not-exist" / "codex"
    with patch("codex_cli_bin.bundled_codex_path", return_value=missing_path):
        r = await _ensure_codex_installed()
    assert r["installed"] is False
    assert r["action"] == "install_failed"
    assert str(missing_path) in r["reason"]
    assert "Settings" in r["reason"] and "Plugins" in r["reason"]


@pytest.mark.asyncio
async def test_no_more_blocked_action_in_cloud_mode():
    """Pre-2026-06-08 the cloud mode returned ``action == "blocked"``
    refusing the npm install. That action is gone — the wheel-based
    install works identically in cloud and local. This regression
    guard ensures we don't accidentally reintroduce the path."""
    fake_binary = Path("/tmp/fake-codex-binary-cloud-test")
    fake_binary.write_text("")
    with patch("codex_cli_bin.bundled_codex_path", return_value=fake_binary):
        # Even if we monkey-patch deployment_mode to cloud, the wheel
        # path doesn't consult it.
        r = await _ensure_codex_installed()
    fake_binary.unlink()
    assert r["action"] != "blocked"


@pytest.mark.asyncio
async def test_no_more_auto_installed_action():
    """``auto_installed`` was the post-npm-install success marker. The
    wheel path never reports this — successful verification is always
    ``already_installed`` because the binary was placed there by uv
    sync at deploy time, not by this function at runtime."""
    fake_binary = Path("/tmp/fake-codex-binary-auto-test")
    fake_binary.write_text("")
    with patch("codex_cli_bin.bundled_codex_path", return_value=fake_binary):
        r = await _ensure_codex_installed()
    fake_binary.unlink()
    assert r["action"] != "auto_installed"
    assert r["action"] == "already_installed"
