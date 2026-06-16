"""
@file_name: test_is_cloud_mode.py
@author: NarraNexus
@date: 2026-06-11
@description: Pin backend.auth._is_cloud_mode precedence.

The canonical deployment-mode resolver (utils/deployment_mode) honors an
explicit NARRANEXUS_DEPLOYMENT_MODE first, then falls back to the
DATABASE_URL heuristic. backend/auth.py had its own divergent copy that
ignored the env var — so a sqlite + NARRANEXUS_DEPLOYMENT_MODE=cloud local
smoke could not run cloud semantics (netmind-login 404'd). This pins the
fixed precedence WITHOUT regressing the Tauri-dmg safety rule (empty /
sqlite DATABASE_URL and no explicit env => local) or the DB_HOST fallback.
"""
from __future__ import annotations

import pytest

from backend.auth import _is_cloud_mode

_ENVS = ("NARRANEXUS_DEPLOYMENT_MODE", "DATABASE_URL", "DB_HOST")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in _ENVS:
        monkeypatch.delenv(k, raising=False)


def test_explicit_cloud_wins_over_sqlite(monkeypatch):
    # The local-smoke case: sqlite DB but explicitly cloud.
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////tmp/x.db")
    assert _is_cloud_mode() is True


def test_explicit_local_wins_over_mysql(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@host:3306/db")
    assert _is_cloud_mode() is False


def test_explicit_is_case_insensitive_and_trimmed(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "  Cloud ")
    assert _is_cloud_mode() is True


def test_heuristic_mysql_is_cloud(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@host:3306/db")
    assert _is_cloud_mode() is True


def test_heuristic_sqlite_is_local_dmg_safety(monkeypatch):
    # No explicit env: empty/sqlite DATABASE_URL MUST stay local (dmg lesson).
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////tmp/x.db")
    assert _is_cloud_mode() is False


def test_db_host_fallback_is_cloud(monkeypatch):
    # Split-var deploy form: DB_HOST set, no DATABASE_URL, no explicit env.
    monkeypatch.setenv("DB_HOST", "rds.example.com")
    assert _is_cloud_mode() is True


def test_nothing_set_is_local(monkeypatch):
    assert _is_cloud_mode() is False
