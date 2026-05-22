"""
@file_name: test_logging_resilient_dir.py
@author: Bin Liang
@date: 2026-05-22
@description: setup_logging must (a) create/repair the CORRECT log dir, and
(b) never crash a service over a bad one.

Root cause of a fresh-Mac "Connection failed": the bundled python's
setup_logging did `~/.narranexus/logs/<svc>`.mkdir() with no guard. When that
dir was unwritable (perms too tight, created by root, or carried over by
Migration Assistant with another Mac's uid), the PermissionError propagated and
killed sqlite_proxy/backend on startup → the DB never came up.

The fix prefers fixing the real dir (chmod self-repair when we own it), and only
falls back to a temp dir when ownership is foreign (needs sudo) — never crashing.
"""
import os
import tempfile
from pathlib import Path

import pytest

from xyz_agent_context.utils.logging import _setup
from xyz_agent_context.utils.logging._setup import _ensure_writable_log_dir


def test_writable_preferred_dir_is_used(tmp_path):
    preferred = tmp_path / "svc"
    got, ok = _ensure_writable_log_dir(preferred, "svc")
    assert ok is True
    assert got == preferred
    assert preferred.is_dir()


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs POSIX ownership + non-root")
def test_repairs_owned_dir_with_bad_perms(tmp_path, monkeypatch):
    """The preferred location exists but is owned-by-us with too-tight perms:
    we should chmod-repair it and use the CORRECT dir, not divert to temp."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    bad = tmp_path / ".narranexus"
    bad.mkdir()
    os.chmod(bad, 0o500)  # r-x: can't create children
    preferred = bad / "logs" / "svc"
    try:
        got, ok = _ensure_writable_log_dir(preferred, "svc")
        assert ok is True
        assert got == preferred, "should repair + use the correct dir, not fall back"
        assert preferred.is_dir()
    finally:
        os.chmod(bad, 0o700)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs POSIX ownership + non-root")
def test_falls_back_to_temp_when_repair_impossible(tmp_path, monkeypatch):
    """Simulate foreign ownership (repair can't help) → fall back to temp,
    still usable, never crash."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(_setup, "_chmod_repair", lambda _t: False)  # can't fix
    bad = tmp_path / ".narranexus"
    bad.mkdir()
    os.chmod(bad, 0o500)
    preferred = bad / "logs" / "svc2"
    try:
        got, ok = _ensure_writable_log_dir(preferred, "svc2")
        assert ok is True
        assert str(got).startswith(tempfile.gettempdir())
        assert got.is_dir()
    finally:
        os.chmod(bad, 0o700)


def test_setup_logging_does_not_raise_on_bad_dir(tmp_path, monkeypatch):
    """End-to-end: a bad NEXUS_LOG_DIR must not raise out of setup_logging."""
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("needs POSIX ownership + non-root")
    monkeypatch.setattr(_setup, "_chmod_repair", lambda _t: False)
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)
    monkeypatch.setenv("NEXUS_LOG_DIR", str(ro / "locked"))
    try:
        result = _setup.setup_logging("ztest_resilient_unique")
        assert isinstance(result, Path)  # returned a path, did not crash
    finally:
        os.chmod(ro, 0o700)
