"""
@file_name: test_factory_gating.py
@date: 2026-06-08
@description: get_analytics() returns NullSink unless enabled+keyed+non-cloud.
track()/identify_user() are best-effort and never raise.
"""
import importlib

import pytest

import xyz_agent_context.analytics as analytics


def _reload(monkeypatch, *, enabled, key, surface):
    monkeypatch.setenv("NARRA_ANALYTICS_ENABLED", enabled)
    if key is None:
        monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    else:
        monkeypatch.setenv("POSTHOG_API_KEY", key)
    monkeypatch.setenv("NARRA_SURFACE", surface)
    importlib.reload(importlib.import_module("xyz_agent_context.analytics.surface"))
    importlib.reload(analytics)
    return analytics


def test_disabled_returns_null_sink(monkeypatch):
    a = _reload(monkeypatch, enabled="false", key="phc_x", surface="local")
    assert type(a.get_analytics()).__name__ == "NullSink"


def test_missing_key_returns_null_sink(monkeypatch):
    a = _reload(monkeypatch, enabled="true", key=None, surface="local")
    assert type(a.get_analytics()).__name__ == "NullSink"


def test_cloud_surface_returns_null_sink_this_phase(monkeypatch):
    a = _reload(monkeypatch, enabled="true", key="phc_x", surface="cloud")
    assert type(a.get_analytics()).__name__ == "NullSink"


def test_enabled_local_with_key_returns_posthog_sink(monkeypatch):
    a = _reload(monkeypatch, enabled="true", key="phc_x", surface="local")
    assert type(a.get_analytics()).__name__ == "PostHogSink"


@pytest.mark.asyncio
async def test_track_never_raises_even_if_sink_throws(monkeypatch):
    a = _reload(monkeypatch, enabled="false", key=None, surface="local")

    class Boom:
        def capture(self, **k):
            raise RuntimeError("boom")
        def identify(self, **k):
            raise RuntimeError("boom")
        def flush(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(a, "_get_sink_cached", lambda: Boom())
    # Must swallow — no raise.
    await a.track(user_id="u1", event="agent_created", properties={})
    await a.identify_user(user_id="u1", traits={})
