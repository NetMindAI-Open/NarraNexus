"""
@file_name: test_claude_status_route.py
@date: 2026-09-09
@description: Tests for the get_claude_status route function's expiry check.

/codex-status already gained an expiry check (incident 2026-06-11): a
present credentials file is not proof a session works, so an expired
token must flip logged_in to False and surface expired=True. This mirror
route filled expires_at from the legacy credentials file but never
compared it against "now" (B-25 / GitHub #111) — this file covers that
gap using the same `_expiry_is_past` helper /codex-status already relies
on. We don't spin up a real FastAPI server — we call the route handler
directly with a mock Request, same pattern as test_codex_status_route.py.

Two things the review of the first version required:

* The CLI probe (`_run_json_subprocess`) is stubbed. `shutil.which` is
  patched to a fixed path, so on a machine where that path really holds
  `claude` the route would spawn `claude auth status` and the answer
  would depend on THAT machine's login — an environment-dependent red.
* Real credentials carry epoch MILLISECONDS (`claudeAiOauth.expiresAt`),
  the one format the fix has to get right; ISO strings alone would stay
  green with the ms/seconds split deleted.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PAST_MS = 1577836800000    # 2020-01-01T00:00:00Z
FUTURE_MS = 4102444800000  # 2100-01-01T00:00:00Z


def _mock_request(role: str = "user") -> MagicMock:
    req = MagicMock()
    req.state.role = role
    return req


@pytest.fixture
def local_mode(monkeypatch):
    """Local deployment, no CLI probe, no env override of the credentials path."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    # `_is_cloud()` reads the explicit mode BEFORE the DATABASE_URL heuristic;
    # a shell that exports it would otherwise route every test to `allowed: False`.
    monkeypatch.delenv("NARRANEXUS_DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_HOME", raising=False)
    monkeypatch.setattr("backend.routes.providers._run_json_subprocess", AsyncMock(return_value=None))


def _write_creds(home: Path, expires_at) -> None:
    """The legacy ~/.claude/.credentials.json, as the CLI writes it."""
    creds_dir = home / ".claude"
    creds_dir.mkdir(parents=True, exist_ok=True)
    value = f'"{expires_at}"' if isinstance(expires_at, str) else str(expires_at)
    (creds_dir / ".credentials.json").write_text(
        '{"claudeAiOauth": {"accessToken": "tok", "expiresAt": ' + value + '}}'
    )


async def _status(monkeypatch, tmp_path, expires_at) -> dict:
    from backend.routes.providers import get_claude_status

    _write_creds(tmp_path, expires_at)
    # `resolve_claude_credentials_path` expands `~` through HOME (os.path.expanduser).
    monkeypatch.setenv("HOME", str(tmp_path))
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        resp = await get_claude_status(_mock_request())
    return resp["data"]


# ── the legacy credentials file: epoch-ms is the real format ────────────────


@pytest.mark.asyncio
async def test_expired_epoch_ms_credentials_report_not_logged_in(local_mode, tmp_path, monkeypatch):
    """A present .credentials.json with an expiry in the PAST must report
    logged_in=False and expired=True — before this fix expires_at was
    filled but never compared, so an expired token still read as
    logged_in=True (GitHub #111)."""
    d = await _status(monkeypatch, tmp_path, PAST_MS)
    assert d["cli_installed"] is True
    assert d["logged_in"] is False
    assert d["expired"] is True


@pytest.mark.asyncio
async def test_valid_epoch_ms_credentials_report_logged_in(local_mode, tmp_path, monkeypatch):
    """A not-yet-expired credentials file must keep reporting logged_in=True
    and expired=False — the expiry check must not false-positive on a
    working session (a year-2100 ms value read as SECONDS would be year
    ~132,000 — still future — so this pairs with the past case above)."""
    d = await _status(monkeypatch, tmp_path, FUTURE_MS)
    assert d["logged_in"] is True
    assert d["expired"] is False


@pytest.mark.asyncio
async def test_iso_expiry_is_compared_too(local_mode, tmp_path, monkeypatch):
    assert (await _status(monkeypatch, tmp_path, "2020-01-01T00:00:00Z"))["expired"] is True
    assert (await _status(monkeypatch, tmp_path, "2099-01-01T00:00:00Z"))["expired"] is False


@pytest.mark.asyncio
async def test_unparseable_expiry_fails_open_logged_in(local_mode, tmp_path, monkeypatch):
    """An expiry we can't confidently parse must NOT be reported as
    expired — fail open, matching /codex-status's rule (under-warn rather
    than wrongly lock out a working session)."""
    d = await _status(monkeypatch, tmp_path, "sometime-soon")
    assert d["logged_in"] is True
    assert d["expired"] is False


# ── the CLI probe path: the same comparison applies to what the CLI says ────


@pytest.mark.asyncio
async def test_an_expired_token_reported_by_the_cli_probe_is_not_logged_in(local_mode, tmp_path, monkeypatch):
    """`claude auth status` answering loggedIn=true with a past expiresAt is
    the CLI's cache talking; the token is dead. The comparison must apply
    to this path as well, not only to the legacy file."""
    from backend.routes.providers import get_claude_status

    monkeypatch.setenv("HOME", str(tmp_path))  # no legacy file at all
    monkeypatch.setattr(
        "backend.routes.providers._run_json_subprocess",
        AsyncMock(return_value={"loggedIn": True, "email": "a@example.com", "expiresAt": PAST_MS}),
    )
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        d = (await get_claude_status(_mock_request()))["data"]
    assert d["email"] == "a@example.com"
    assert d["logged_in"] is False
    assert d["expired"] is True


# ── the credentials file is the one the RUNTIME reads ───────────────────────


@pytest.mark.asyncio
async def test_the_legacy_file_is_resolved_through_the_runtime_override(local_mode, tmp_path, monkeypatch):
    """The agents read their credentials through driver/derive.py, which
    honours CLAUDE_CLI_HOME. A status route hard-wired to ~/.claude judged
    expiry against a file the runtime never used whenever that override
    was set (/codex-status already honours CODEX_HOME)."""
    from backend.routes.providers import get_claude_status

    monkeypatch.setenv("HOME", str(tmp_path))
    _write_creds(tmp_path, PAST_MS)  # ~/.claude: expired — must NOT be consulted
    cli_home = tmp_path / "cli-home"
    cli_home.mkdir()
    (cli_home / ".credentials.json").write_text(
        '{"claudeAiOauth": {"accessToken": "tok", "expiresAt": ' + str(FUTURE_MS) + '}}'
    )
    monkeypatch.setenv("CLAUDE_CLI_HOME", str(cli_home))
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        d = (await get_claude_status(_mock_request()))["data"]
    assert d["logged_in"] is True and d["expired"] is False
