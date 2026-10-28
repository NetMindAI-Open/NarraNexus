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
import ipaddress
import socket

import pytest

from narranexus_plugins.home_assistant_module._home_assistant_impl.binding import (
    NOT_CONFIGURED,
    resolve_client,
)
from narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client import (
    HAClient,
    HAError,
    validate_base_url,
)
from narranexus.platform.schema.home_assistant_schema import HAConfig


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """Keep the URL guard off the network.

    `validate_base_url` resolves every host through `socket.getaddrinfo`, so a
    test that passes a NAME does a live lookup: `homeassistant.local` is mDNS
    and `x.ui.nabu.casa` is a real domain. That was 5 seconds of the suite on
    this machine, and worse than slow — the verdict depended on whatever the
    runner's resolver happened to answer, including "nothing" in a sandbox.

    Numeric hosts echo themselves, because the SSRF cases are asserting on the
    exact address they passed in; names resolve to a fixed public address,
    which is what those tests mean by "a normal host".
    """
    def _fake_getaddrinfo(host, port, *_args, **_kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # A genuinely global address. The documentation ranges
            # (203.0.113.0/24 and friends) read as `is_private` to Python's
            # ipaddress, which is exactly what the cloud SSRF guard rejects —
            # the obvious "fake public IP" is not one.
            host = "93.184.215.14"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


def _validate(url):
    return asyncio.run(validate_base_url(url))


def test_validate_base_url_accepts_lan_and_https():
    assert _validate("http://homeassistant.local:8123") == "http://homeassistant.local:8123"
    assert _validate("https://x.ui.nabu.casa/") == "https://x.ui.nabu.casa"  # trailing slash stripped


def test_validate_base_url_rejects_non_http():
    for bad in ("ftp://x", "file:///etc/passwd", "no-scheme"):
        with pytest.raises(HAError):
            _validate(bad)


def test_validate_base_url_rejects_metadata_host():
    with pytest.raises(HAError):
        _validate("http://169.254.169.254/latest/meta-data")


def test_validate_base_url_cloud_blocks_private_ssrf(monkeypatch):
    # In cloud mode the backend shares a network with internal services, so a
    # user-supplied private/loopback host is an SSRF vector — reject it.
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    for internal in ("http://127.0.0.1:8123", "http://192.168.1.10:8123", "http://10.0.0.5:8000"):
        with pytest.raises(HAError):
            _validate(internal)
    # A public host is still fine on cloud.
    assert _validate("https://x.ui.nabu.casa") == "https://x.ui.nabu.casa"


def test_validate_base_url_reads_the_same_mode_source_as_the_egress_wrapper(monkeypatch):
    """HA's cloud/local branch and the shared wrapper must decide the mode from
    ONE symbol (deployment_mode.is_cloud_mode). If HA kept its own bound copy,
    the two halves could disagree and the gate would silently not run."""
    from narranexus.platform.utils import deployment_mode

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    monkeypatch.setattr(deployment_mode, "is_cloud_mode", lambda: True)
    with pytest.raises(HAError):
        _validate("http://192.168.1.10:8123")


def test_validate_base_url_local_allows_lan(monkeypatch):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    assert _validate("http://192.168.1.10:8123") == "http://192.168.1.10:8123"


def test_validate_base_url_cloud_rejects_docker_service_name(monkeypatch):
    """A docker-network name (broker / quota-api) resolves internal → refused."""
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")

    def _resolve(host, port, *_a, **_k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.20.0.9", port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _resolve)
    with pytest.raises(HAError) as exc:
        _validate("http://broker:8020")
    assert "172.20" not in str(exc.value)


def test_validate_base_url_cloud_dns_failure_rejects(monkeypatch):
    """DNS failure used to PASS the guard; it must reject (fail closed)."""
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")

    def _fail(*_a, **_k):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    with pytest.raises(HAError):
        _validate("http://does-not-resolve.example")


class _FakeHAResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.content_type = "application/json"
        self._payload = payload if payload is not None else {"message": "API running."}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return "UPSTREAM-HA-BODY"


class _FakeHASessions:
    """Stands in for aiohttp.ClientSession; records every construction + request."""

    def __init__(self):
        self.sessions = 0
        self.requests: list[dict] = []
        self.response = _FakeHAResponse()
        self.raise_exc = None

    def install(self, monkeypatch, hc):
        recorder = self

        class _Session:
            def __init__(self, *a, **k):
                recorder.sessions += 1

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def request(self, method, url, **kw):
                recorder.requests.append({"method": method, "url": url, **kw})
                if recorder.raise_exc is not None:
                    raise recorder.raise_exc
                return recorder.response

        monkeypatch.setattr(hc.aiohttp, "ClientSession", _Session)
        monkeypatch.setattr(hc.aiohttp, "TCPConnector", lambda *a, **k: None)


def test_cloud_internal_host_refused_before_any_request(monkeypatch):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    fake = _FakeHASessions()
    fake.install(monkeypatch, hc)

    client = HAClient("http://172.20.0.18:8123", "tok")
    with pytest.raises(HAError):
        asyncio.run(client.ping())
    assert fake.sessions == 0 and fake.requests == []


def test_cloud_public_host_requests_without_following_redirects(monkeypatch):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    fake = _FakeHASessions()
    fake.install(monkeypatch, hc)

    assert asyncio.run(HAClient("https://x.ui.nabu.casa", "tok").ping()) is True
    assert fake.requests[0]["url"] == "https://x.ui.nabu.casa/api/"
    assert fake.requests[0]["allow_redirects"] is False


def test_redirect_is_not_followed_and_reported_generically(monkeypatch):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    fake = _FakeHASessions()
    fake.response = _FakeHAResponse(status=302)
    fake.install(monkeypatch, hc)

    with pytest.raises(HAError) as exc:
        asyncio.run(HAClient("https://x.ui.nabu.casa", "tok").ping())
    assert "302" in str(exc.value)
    assert len(fake.requests) == 1


def test_network_error_text_is_not_echoed(monkeypatch):
    import aiohttp

    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    fake = _FakeHASessions()
    fake.raise_exc = aiohttp.ClientError("connect to 10.1.2.3:8123 refused SECRET-DETAIL")
    fake.install(monkeypatch, hc)

    with pytest.raises(HAError) as exc:
        asyncio.run(HAClient("https://x.ui.nabu.casa", "tok").ping())
    assert "SECRET-DETAIL" not in str(exc.value)
    assert "10.1.2.3" not in str(exc.value)


def _call_test_route(monkeypatch, base_url):
    import narranexus_plugins.home_assistant_module.routes as r

    async def _uid(_request):
        return "u1"

    monkeypatch.setattr(r, "current_user_id", _uid)
    body = r.HATestBody(base_url=base_url, token="tok")
    return asyncio.run(r.test_connection(object(), body))


def test_test_route_returns_generic_error_for_internal_host(monkeypatch):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    fake = _FakeHASessions()
    fake.install(monkeypatch, hc)

    out = _call_test_route(monkeypatch, "http://10.0.0.5:8123")
    assert out["ok"] is False
    assert "10.0.0.5" not in out["error"]
    assert fake.requests == []


def test_test_route_never_echoes_unexpected_exception_text(monkeypatch):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")

    async def _boom(self):
        raise RuntimeError("internal traceback detail SECRET-XYZ")

    monkeypatch.setattr(hc.HAClient, "ping", _boom)
    out = _call_test_route(monkeypatch, "https://x.ui.nabu.casa")
    assert out["ok"] is False
    assert "SECRET-XYZ" not in out["error"]


def test_require_agent_owner_enforced(monkeypatch):
    # Cross-tenant guard: authenticated ≠ authorized. A user may only touch an
    # agent they own; otherwise 403 (IDOR fix). Local mode (no user_id) is open.
    # The route delegates to the canonical helper (backend/routes/_ownership),
    # so this stubs the helper's own seams the same way test_ownership.py does.
    import asyncio

    from fastapi import HTTPException

    import backend.routes._ownership as own
    import narranexus_plugins.home_assistant_module.routes as r

    class _Req:
        def __init__(self, uid):
            self.state = type("S", (), {"user_id": uid})()

    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)

    owners = {}

    async def _resolve(self, agent_id):
        return owners.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    # A plugin router calls the ``contracts.web.WebHost`` seam; a test that
    # builds its own app instead of going through ``backend.plugins_host``
    # must publish the host itself (the seam fails loud, never open).
    from backend.plugin_sdk_host import install_web_host

    install_web_host()


    async def run():
        owners["agent_x"] = "u1"
        # Owner matches → no raise.
        await r.require_agent_owner(_Req("u1"), "agent_x")
        # Different owner → 403.
        try:
            await r.require_agent_owner(_Req("u2"), "agent_x")
            raise AssertionError("expected 403")
        except HTTPException as e:
            assert e.status_code == 403
        # Agent missing → 404.
        try:
            await r.require_agent_owner(_Req("u1"), "ghost")
            raise AssertionError("expected 404")
        except HTTPException as e:
            assert e.status_code == 404
        # Ownership LOOKUP failure → 503 (infrastructure fault, not "not found").
        owners["agent_x"] = None
        try:
            await r.require_agent_owner(_Req("u1"), "agent_x")
            raise AssertionError("expected 503")
        except HTTPException as e:
            assert e.status_code == 503
        owners["agent_x"] = "u1"
        # Local mode (no user_id) → not enforced.
        await r.require_agent_owner(_Req(None), "agent_x")

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
    import narranexus_plugins.home_assistant_module._home_assistant_impl.binding as b

    monkeypatch.setattr(b, "HomeAssistantBindingRepository", _StubBindingRepo)

    # resolve_client no longer takes a db — it reads through the
    # ChannelCredentialStore seam. Env unset -> DirectStore -> the
    # (monkeypatched) HomeAssistantBindingRepository, so the stub still drives
    # the "no binding row" scenario.
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)

    async def run():
        client, msg = await resolve_client(agent_id="a1")
        assert client is None
        assert msg == NOT_CONFIGURED

    asyncio.run(run())
def test_verify_route_never_echoes_unexpected_exception_text(monkeypatch):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.binding as b
    import narranexus_plugins.home_assistant_module.routes as r

    async def _uid(_request):
        return "u1"

    async def _db():
        return object()

    async def _owner(_request, _agent_id):
        return None

    class _Client:
        async def ping(self):
            raise RuntimeError("internal traceback detail SECRET-VERIFY")

    async def _resolve(agent_id):
        return _Client(), None

    monkeypatch.setattr(r, "current_user_id", _uid)
    monkeypatch.setattr(r, "get_db_client", _db)
    monkeypatch.setattr(r, "require_agent_owner", _owner)
    monkeypatch.setattr(b, "resolve_client", _resolve)

    out = asyncio.run(r.verify_binding(object(), r.HAVerifyBody(agent_id="a1")))
    assert out["ok"] is False
    assert "SECRET-VERIFY" not in out["error"]


@pytest.mark.parametrize("url", ["http://169.254.10.10:8123", "http://[fe80::1]:8123"])
def test_local_mode_refuses_literal_link_local(monkeypatch, url):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    with pytest.raises(HAError):
        _validate(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://2852039166",            # decimal form of 169.254.169.254
        "http://169.254.169.254.",      # trailing-dot literal
        "http://instance-data",         # EC2 name for the metadata service
    ],
)
def test_local_mode_refuses_names_resolving_link_local(monkeypatch, url):
    """Local keeps LAN hosts allowed but still resolves the host and refuses
    any link-local answer — spellings that slip past a literal-only check."""
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")

    def _resolve(host, port, *_a, **_k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _resolve)
    with pytest.raises(HAError):
        _validate(url)


def test_local_mode_lan_name_resolving_private_still_allowed(monkeypatch):
    import narranexus_plugins.home_assistant_module._home_assistant_impl.ha_client as hc

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")

    def _resolve(host, port, *_a, **_k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _resolve)
    assert _validate("http://homeassistant.local:8123") == "http://homeassistant.local:8123"
