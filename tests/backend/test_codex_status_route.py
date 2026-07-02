"""
@file_name: test_codex_status_route.py
@date: 2026-05-30
@description: Tests for the get_codex_status route function.

We don't spin up a real FastAPI server — we call the route handler
directly with a mock Request. ``shutil.which`` is patched so the
test doesn't depend on whether ``codex`` is installed in the CI
container.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def _mock_request(role: str = "user") -> MagicMock:
    """Build a minimal Request stand-in carrying request.state.role."""
    req = MagicMock()
    req.state.role = role
    return req


@pytest.mark.asyncio
async def test_returns_logged_in_when_auth_file_exists(tmp_path, monkeypatch):
    from backend.routes.providers import get_codex_status

    auth = tmp_path / "auth.json"
    auth.write_text('{"chatgpt":{"email":"tong@example.com"},"token":{"expiresAt":"2026-12-31T00:00:00Z"}}')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    # Force local mode by ensuring DATABASE_URL is sqlite
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", side_effect=lambda x: "/usr/local/bin/codex" if x == "codex" else None):
        resp = await get_codex_status(_mock_request())

    assert resp["success"] is True
    d = resp["data"]
    assert d["cli_installed"] is True
    assert d["logged_in"] is True
    assert d["email"] == "tong@example.com"
    assert "2026-12-31" in (d["expires_at"] or "")


@pytest.mark.asyncio
async def test_returns_not_logged_in_when_auth_file_missing(tmp_path, monkeypatch):
    from backend.routes.providers import get_codex_status

    # Empty CODEX_HOME — no auth.json
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", side_effect=lambda x: "/usr/local/bin/codex" if x == "codex" else None):
        resp = await get_codex_status(_mock_request())

    d = resp["data"]
    assert d["cli_installed"] is True
    assert d["logged_in"] is False
    assert d["email"] is None


@pytest.mark.asyncio
async def test_returns_cli_not_installed(tmp_path, monkeypatch):
    from backend.routes.providers import get_codex_status

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", return_value=None):
        resp = await get_codex_status(_mock_request())

    d = resp["data"]
    assert d["cli_installed"] is False
    assert d["logged_in"] is False


@pytest.mark.asyncio
async def test_cloud_mode_hides_status_from_non_staff(monkeypatch):
    """Mirror of /claude-status behaviour: cloud mode hides the
    card unless the caller is a staff user."""
    from backend.routes.providers import get_codex_status

    # Postgres URL → cloud mode
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")

    resp = await get_codex_status(_mock_request(role="user"))

    d = resp["data"]
    assert d["cli_installed"] is False
    assert d["logged_in"] is False
    assert d.get("allowed") is False  # hidden from non-staff


@pytest.mark.asyncio
async def test_cloud_mode_allows_staff(tmp_path, monkeypatch):
    """Staff users see the real status even in cloud mode."""
    from backend.routes.providers import get_codex_status

    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")

    with patch("shutil.which", side_effect=lambda x: "/usr/local/bin/codex" if x == "codex" else None):
        resp = await get_codex_status(_mock_request(role="staff"))

    d = resp["data"]
    assert d["cli_installed"] is True
    assert d["logged_in"] is True


@pytest.mark.asyncio
async def test_unparseable_auth_file_still_reports_logged_in(tmp_path, monkeypatch):
    """If auth.json exists but isn't JSON we can decode, still treat
    as logged_in (codex itself would do the same) — just leave email
    + expires_at None."""
    from backend.routes.providers import get_codex_status

    auth = tmp_path / "auth.json"
    auth.write_text("not valid json at all")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", side_effect=lambda x: "/usr/local/bin/codex" if x == "codex" else None):
        resp = await get_codex_status(_mock_request())

    d = resp["data"]
    assert d["logged_in"] is True
    assert d["email"] is None
    assert d["expires_at"] is None


@pytest.mark.asyncio
async def test_expired_token_reports_not_logged_in(tmp_path, monkeypatch):
    """Honesty fix (incident 2026-06-11): a present auth.json with an
    expiry in the PAST must report logged_in=False so the UI prompts
    re-login — previously it reported logged_in=True (file existence
    only) and the Settings page showed "✓ auth ready" on a dead session."""
    from backend.routes.providers import get_codex_status

    auth = tmp_path / "auth.json"
    # Expired well in the past.
    auth.write_text('{"token":{"expiresAt":"2020-01-01T00:00:00Z"}}')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", side_effect=lambda x: "/usr/local/bin/codex" if x == "codex" else None):
        resp = await get_codex_status(_mock_request())

    d = resp["data"]
    assert d["cli_installed"] is True
    assert d["logged_in"] is False
    assert d["expired"] is True


@pytest.mark.asyncio
async def test_unparseable_expiry_fails_open_logged_in(tmp_path, monkeypatch):
    """A present file with an expiry we CAN'T confidently parse must NOT
    be reported as expired — fail open (under-warn rather than wrongly
    lock out a working session)."""
    from backend.routes.providers import get_codex_status

    auth = tmp_path / "auth.json"
    auth.write_text('{"token":{"expiresAt":"sometime-soon"}}')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", side_effect=lambda x: "/usr/local/bin/codex" if x == "codex" else None):
        resp = await get_codex_status(_mock_request())

    d = resp["data"]
    assert d["logged_in"] is True
    assert d["expired"] is False


def test_expiry_is_past_helper():
    """Unit-cover the parser: epoch seconds / ms / ISO, past vs future,
    and fail-open on garbage."""
    from datetime import datetime, timezone, timedelta

    from backend.routes.providers import _expiry_is_past

    now = datetime.now(timezone.utc)
    assert _expiry_is_past(int((now - timedelta(days=1)).timestamp())) is True
    assert _expiry_is_past(int((now + timedelta(days=1)).timestamp())) is False
    assert _expiry_is_past(int((now - timedelta(days=1)).timestamp() * 1000)) is True
    assert _expiry_is_past((now - timedelta(hours=1)).isoformat()) is True
    assert _expiry_is_past((now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")) is False
    assert _expiry_is_past("not-a-date") is False
    assert _expiry_is_past(None) is False
