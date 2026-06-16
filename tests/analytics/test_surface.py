"""
@file_name: test_surface.py
@date: 2026-06-08
@description: resolve_surface() reads NARRA_SURFACE; defaults to "local".
"""
import importlib

import xyz_agent_context.analytics.surface as surface_mod


def _fresh(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("NARRA_SURFACE", raising=False)
    else:
        monkeypatch.setenv("NARRA_SURFACE", value)
    importlib.reload(surface_mod)
    return surface_mod


def test_default_is_local_when_unset(monkeypatch):
    m = _fresh(monkeypatch, None)
    assert m.resolve_surface() == "local"


def test_desktop_from_env(monkeypatch):
    m = _fresh(monkeypatch, "desktop")
    assert m.resolve_surface() == "desktop"


def test_unknown_value_falls_back_to_local(monkeypatch):
    m = _fresh(monkeypatch, "weird")
    assert m.resolve_surface() == "local"
