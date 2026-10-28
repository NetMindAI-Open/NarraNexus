"""
@file_name: test_cli_binary_managed.py
@author: NarraNexus
@date: 2026-08-28
@description: The Claude binary resolver prefers the managed plugin binary
              (~/.narranexus/plugins/nodejs/.../claude) over PATH, gated by the
              same version pin. Delete the managed-path branch in
              ``cli_binary._decide`` and one of these goes red.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.frameworks_claude_code import cli_binary
from narranexus.platform.agent_framework import plugin_paths


@pytest.fixture()
def prefer_pinned(monkeypatch):
    """Ensure the gate is on and no explicit override is set."""
    from narranexus.platform.settings import settings

    monkeypatch.setattr(settings, "claude_cli_prefer_pinned", True, raising=False)
    monkeypatch.setattr(settings, "claude_cli_path", "", raising=False)


def test_managed_binary_preferred_when_pin_matches(prefer_pinned, tmp_path, monkeypatch):
    managed = tmp_path / "nodejs" / "node_modules" / ".bin" / "claude"
    managed.parent.mkdir(parents=True)
    managed.write_text("#!/bin/sh\n")
    monkeypatch.setattr(plugin_paths, "claude_cli_path", lambda: managed)

    # Managed reports the pin; PATH lookup would be a different (wrong) binary.
    monkeypatch.setattr(
        cli_binary, "_probe_version", lambda p: cli_binary.PINNED_CLI_VERSION
    )
    called = {"which": False}

    def _which(_):
        called["which"] = True
        return "/usr/bin/claude"

    monkeypatch.setattr(cli_binary.shutil, "which", _which)

    path, version, reason = cli_binary._decide()
    assert path == str(managed)
    assert version == cli_binary.PINNED_CLI_VERSION
    assert "managed" in reason
    # Preferred BEFORE consulting PATH.
    assert called["which"] is False


def test_managed_wrong_version_falls_through_to_path(prefer_pinned, tmp_path, monkeypatch):
    managed = tmp_path / "nodejs" / "node_modules" / ".bin" / "claude"
    managed.parent.mkdir(parents=True)
    managed.write_text("#!/bin/sh\n")
    monkeypatch.setattr(plugin_paths, "claude_cli_path", lambda: managed)

    # Managed is an unvalidated version → must NOT be used; PATH has the pin.
    def _probe(p):
        return "1.0.0" if p == str(managed) else cli_binary.PINNED_CLI_VERSION

    monkeypatch.setattr(cli_binary, "_probe_version", _probe)
    monkeypatch.setattr(cli_binary.shutil, "which", lambda _: "/usr/bin/claude")

    path, version, reason = cli_binary._decide()
    assert path == "/usr/bin/claude"
    assert version == cli_binary.PINNED_CLI_VERSION


def test_no_managed_no_path_falls_back_to_bundled(prefer_pinned, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_paths, "claude_cli_path", lambda: tmp_path / "absent")
    monkeypatch.setattr(cli_binary.shutil, "which", lambda _: None)

    path, version, reason = cli_binary._decide()
    assert path is None  # None => SDK bundled binary
