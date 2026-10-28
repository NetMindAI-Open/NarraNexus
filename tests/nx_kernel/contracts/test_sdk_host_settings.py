"""
@file_name: test_sdk_host_settings.py
@author: Bin Liang
@date: 2026-09-11
@description: ``sdk.web.host_settings()`` answers in every process role — from the WebHost when one is exposed, from the environment otherwise.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.services import WEB_HOST
from narranexus.contracts.web import DEFAULT_MAX_UPLOAD_BYTES, MAX_UPLOAD_BYTES_ENV
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.sdk.web import host_settings


@pytest.fixture
def no_http_host(monkeypatch):
    """The workers / MCP topology: nothing exposed under ``host.web``."""
    monkeypatch.delitem(KERNEL_REGISTRIES.services._services, WEB_HOST.id, raising=False)


def test_without_an_http_host_the_default_ceiling_applies(no_http_host, monkeypatch):
    monkeypatch.delenv(MAX_UPLOAD_BYTES_ENV, raising=False)
    assert host_settings().max_upload_bytes == DEFAULT_MAX_UPLOAD_BYTES


def test_without_an_http_host_the_env_override_applies(no_http_host, monkeypatch):
    monkeypatch.setenv(MAX_UPLOAD_BYTES_ENV, "1234")
    assert host_settings().max_upload_bytes == 1234


def test_with_an_http_host_its_settings_win(monkeypatch):
    import backend.main  # noqa: F401 — installs the backend WebHost
    from backend.config import settings

    monkeypatch.setattr(settings, "max_upload_bytes", 777)
    assert host_settings().max_upload_bytes == 777


def test_backend_and_fallback_resolve_the_same_ceiling(no_http_host):
    # backend.config reads the env once at import; the fallback reads it per
    # call. With the env unchanged since import, both must agree.
    from backend.config import settings

    assert host_settings().max_upload_bytes == settings.max_upload_bytes
