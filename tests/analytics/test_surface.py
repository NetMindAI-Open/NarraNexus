"""
@file_name: test_surface.py
@date: 2026-06-08
@description: resolve_surface() prefers NARRA_SURFACE and otherwise derives
cloud/local from the canonical deployment mode.
"""
import importlib

import narranexus.platform.analytics.surface as surface_mod


def _fresh(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("NARRA_SURFACE", raising=False)
    else:
        monkeypatch.setenv("NARRA_SURFACE", value)
    importlib.reload(surface_mod)
    return surface_mod


def test_default_is_local_when_unset(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    m = _fresh(monkeypatch, None)
    assert m.resolve_surface() == "local"


def test_unset_surface_infers_cloud_from_deployment_mode(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    m = _fresh(monkeypatch, None)
    assert m.resolve_surface() == "cloud"


def test_desktop_from_env(monkeypatch):
    m = _fresh(monkeypatch, "desktop")
    assert m.resolve_surface() == "desktop"


def test_unknown_value_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    m = _fresh(monkeypatch, "weird")
    assert m.resolve_surface() == "local"


def test_missing_surface_logs_inferred_value(monkeypatch):
    warnings = []
    monkeypatch.setattr(surface_mod.logger, "warning", warnings.append)
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    monkeypatch.delenv("NARRA_SURFACE", raising=False)

    assert surface_mod.resolve_surface() == "cloud"
    assert warnings and "inferred 'cloud'" in warnings[-1]
