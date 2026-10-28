"""
@file_name: test_deployment.py
@author: Bin Liang
@date: 2026-09-03
@description: One is_cloud_mode: precedence rules, and the three legacy call sites agree with the kernel.
"""
from __future__ import annotations

import pytest

from narranexus.kernel.deployment import get_deployment_mode, is_cloud_mode, is_local_mode

MATRIX = [
    ({}, "local"),
    ({"NARRANEXUS_DEPLOYMENT_MODE": "cloud"}, "cloud"),
    ({"NARRANEXUS_DEPLOYMENT_MODE": " Cloud "}, "cloud"),
    ({"NARRANEXUS_DEPLOYMENT_MODE": "local", "DATABASE_URL": "mysql://x"}, "local"),
    ({"NARRANEXUS_DEPLOYMENT_MODE": "banana", "DATABASE_URL": "mysql://x"}, "cloud"),
    ({"DATABASE_URL": "mysql://u:p@h/db"}, "cloud"),
    ({"DATABASE_URL": "sqlite:////tmp/x.db"}, "local"),
    ({"DATABASE_URL": "SQLite:///x.db", "DB_HOST": "h"}, "local"),
    ({"DB_HOST": "rds.example"}, "cloud"),
    ({"DB_HOST": "   "}, "local"),
]


@pytest.mark.parametrize(("env", "expected"), MATRIX)
def test_precedence_matrix(env, expected):
    assert get_deployment_mode(env) == expected
    assert is_cloud_mode(env) is (expected == "cloud")
    assert is_local_mode(env) is (expected == "local")


@pytest.mark.parametrize(("env", "expected"), MATRIX)
def test_legacy_call_sites_forward_to_the_kernel(monkeypatch, env, expected):
    for key in ("NARRANEXUS_DEPLOYMENT_MODE", "DATABASE_URL", "DB_HOST"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from backend.auth import _is_cloud_mode as auth_is_cloud
    from narranexus.platform.utils import deployment_mode as legacy

    assert legacy.is_cloud_mode is is_cloud_mode
    assert legacy.get_deployment_mode is get_deployment_mode
    assert auth_is_cloud() is (expected == "cloud")


def test_settings_is_cloud_mode_follows_the_kernel(monkeypatch):
    from narranexus.platform.settings import Settings

    for key in ("NARRANEXUS_DEPLOYMENT_MODE", "DATABASE_URL", "DB_HOST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@h/db")
    assert Settings().is_cloud_mode is True
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    assert Settings().is_cloud_mode is False
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    assert Settings().is_cloud_mode is True
