"""
@file_name: test_deployment_mode.py
@author: Bin Liang
@date: 2026-04-20
@description: Deployment-mode detection: single source of truth.

The env var is what cloud deployments set in their `.env`; local installs
simply omit it. We also preserve the old `DATABASE_URL`-heuristic so
existing local sqlite deployments continue to report `local` without
needing to set anything new.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.utils.deployment_mode import (
    DEPLOYMENT_MODE_ENV_VAR,
    POWER_LOGIN_ENV_VAR,
    get_deployment_mode,
    is_cloud_mode,
    is_local_mode,
    is_power_login_enabled,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with NO deployment env var set and the stock
    DATABASE_URL pointing at sqlite (local default)."""
    monkeypatch.delenv(DEPLOYMENT_MODE_ENV_VAR, raising=False)
    monkeypatch.delenv(POWER_LOGIN_ENV_VAR, raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/test.db")
    yield


def test_defaults_to_local_when_env_var_unset():
    assert get_deployment_mode() == "local"
    assert is_local_mode() is True
    assert is_cloud_mode() is False


def test_explicit_cloud_env_var_takes_precedence(monkeypatch):
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "cloud")
    assert get_deployment_mode() == "cloud"
    assert is_cloud_mode() is True
    assert is_local_mode() is False


def test_explicit_local_env_var(monkeypatch):
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "local")
    assert get_deployment_mode() == "local"
    assert is_local_mode() is True


def test_env_var_case_insensitive(monkeypatch):
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "CLOUD")
    assert get_deployment_mode() == "cloud"
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "  Cloud  ")
    assert get_deployment_mode() == "cloud"


def test_env_var_garbage_falls_back_to_local(monkeypatch):
    """Unknown value → default 'local' (safer to constrain less when unsure)."""
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "prod")
    assert get_deployment_mode() == "local"


def test_fallback_heuristic_mysql_url_infers_cloud(monkeypatch):
    """Legacy path: no explicit env var, but a non-sqlite DATABASE_URL
    implies a shared-DB deployment → cloud. Existing cloud deployments
    that never set NARRANEXUS_DEPLOYMENT_MODE still get the right mode."""
    monkeypatch.delenv(DEPLOYMENT_MODE_ENV_VAR, raising=False)
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://u:p@host/db")
    assert get_deployment_mode() == "cloud"


def test_env_var_wins_over_db_url_heuristic(monkeypatch):
    """If a deployment explicitly sets NARRANEXUS_DEPLOYMENT_MODE=local
    on a MySQL instance (e.g. a dev setup), honour the explicit choice."""
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "local")
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://u:p@host/db")
    assert get_deployment_mode() == "local"


# --- power login availability (deployment axis, orthogonal to security regime) ---

def test_power_login_always_on_in_cloud(monkeypatch):
    """Cloud → power login on regardless of the local opt-in flag."""
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "cloud")
    assert is_power_login_enabled() is True


def test_power_login_off_in_local_by_default():
    """Local install with no opt-in → pure-local username login only."""
    assert is_local_mode() is True
    assert is_power_login_enabled() is False


def test_power_login_local_opt_in_enables_it(monkeypatch):
    monkeypatch.setenv(POWER_LOGIN_ENV_VAR, "true")
    assert is_local_mode() is True  # opt-in does NOT flip the security regime
    assert is_cloud_mode() is False
    assert is_power_login_enabled() is True


def test_power_login_opt_in_truthy_spellings(monkeypatch):
    for val in ("1", "true", "TRUE", "  Yes  "):
        monkeypatch.setenv(POWER_LOGIN_ENV_VAR, val)
        assert is_power_login_enabled() is True


def test_power_login_opt_in_garbage_stays_off(monkeypatch):
    monkeypatch.setenv(POWER_LOGIN_ENV_VAR, "maybe")
    assert is_power_login_enabled() is False
