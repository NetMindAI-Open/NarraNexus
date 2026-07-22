"""
@file_name: test_home_assistant.py
@author: NetMind.AI
@date: 2026-07-14
@description: Tests for the Home Assistant integration — the SSRF/URL guard,
config round-trip, and binding resolution (unbound → actionable message).

Network calls to a real HA are not exercised here (no live instance); these
cover the pure logic + the "not connected yet" path a fresh agent hits.
"""

from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.module.home_assistant_module._home_assistant_impl.binding import (
    NOT_CONFIGURED,
    resolve_client,
)
from xyz_agent_context.module.home_assistant_module._home_assistant_impl.ha_client import (
    HAClient,
    HAError,
    validate_base_url,
)
from xyz_agent_context.schema.home_assistant_schema import HAConfig


def test_validate_base_url_accepts_lan_and_https():
    assert validate_base_url("http://homeassistant.local:8123") == "http://homeassistant.local:8123"
    assert validate_base_url("https://x.ui.nabu.casa/") == "https://x.ui.nabu.casa"  # trailing slash stripped


def test_validate_base_url_rejects_non_http():
    for bad in ("ftp://x", "file:///etc/passwd", "no-scheme"):
        with pytest.raises(HAError):
            validate_base_url(bad)


def test_validate_base_url_rejects_metadata_host():
    with pytest.raises(HAError):
        validate_base_url("http://169.254.169.254/latest/meta-data")


def test_validate_base_url_cloud_blocks_private_ssrf(monkeypatch):
    # In cloud mode the backend shares a network with internal services, so a
    # user-supplied private/loopback host is an SSRF vector — reject it.
    import xyz_agent_context.module.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setattr(hc, "is_cloud_mode", lambda: True)
    for internal in ("http://127.0.0.1:8123", "http://192.168.1.10:8123", "http://10.0.0.5:8000"):
        with pytest.raises(HAError):
            hc.validate_base_url(internal)
    # A public host is still fine on cloud.
    assert hc.validate_base_url("https://x.ui.nabu.casa") == "https://x.ui.nabu.casa"


def test_validate_base_url_local_allows_lan(monkeypatch):
    import xyz_agent_context.module.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setattr(hc, "is_cloud_mode", lambda: False)
    assert hc.validate_base_url("http://192.168.1.10:8123") == "http://192.168.1.10:8123"


def test_require_agent_owner_enforced(monkeypatch):
    # Cross-tenant guard: authenticated ≠ authorized. A user may only touch an
    # agent they own; otherwise 403 (IDOR fix). Local mode (no user_id) is open.
    import asyncio

    from fastapi import HTTPException

    import backend.routes.home_assistant as r

    class _Req:
        def __init__(self, uid):
            self.state = type("S", (), {"user_id": uid})()

    class _DB:
        def __init__(self, created_by):
            self._cb = created_by

        async def get_one(self, table, filt):
            return {"agent_id": filt["agent_id"], "created_by": self._cb} if self._cb else None

    async def run():
        # Owner matches → no raise.
        await r._require_agent_owner(_Req("u1"), _DB("u1"), "agent_x")
        # Different owner → 403.
        try:
            await r._require_agent_owner(_Req("u2"), _DB("u1"), "agent_x")
            raise AssertionError("expected 403")
        except HTTPException as e:
            assert e.status_code == 403
        # Agent missing → 404.
        try:
            await r._require_agent_owner(_Req("u1"), _DB(None), "ghost")
            raise AssertionError("expected 404")
        except HTTPException as e:
            assert e.status_code == 404
        # Local mode (no user_id) → not enforced.
        await r._require_agent_owner(_Req(None), _DB("someone"), "agent_x")

    asyncio.run(run())


def test_ha_config_json_round_trip():
    cfg = HAConfig(base_url="http://ha:8123", token="llat_secret", verify_tls=False)
    restored = HAConfig.model_validate_json(cfg.model_dump_json())
    assert restored.base_url == "http://ha:8123"
    assert restored.token == "llat_secret"
    assert restored.verify_tls is False


def test_ha_client_construct_validates_url():
    # Bad URL raises at construction (guarded before any network use).
    with pytest.raises(HAError):
        HAClient("ftp://nope", "tok")


class _StubBindingRepo:
    """HomeAssistantBindingRepository stub: no binding row for the agent."""

    def __init__(self, db):
        pass

    async def get_by_agent(self, agent_id):
        return None


def test_resolve_client_unconfigured(monkeypatch):
    # With no binding row, resolve_client returns the actionable NOT_CONFIGURED
    # message (never raises) so the tool can relay it to the user.
    import xyz_agent_context.module.home_assistant_module._home_assistant_impl.binding as b

    monkeypatch.setattr(b, "HomeAssistantBindingRepository", _StubBindingRepo)

    async def run():
        client, msg = await resolve_client(db=object(), agent_id="a1")
        assert client is None
        assert msg == NOT_CONFIGURED

    asyncio.run(run())
