"""
@file_name: test_provider_egress_gate.py
@date: 2026-10-28
@description: SSRF egress gate on user-supplied provider base_urls.

The provider "test connection" paths (``POST /api/providers/test-config`` and
``POST /api/providers/{id}/test``) and the custom-provider save path make the
SERVER fetch a base_url the user typed. In cloud mode that URL must pass the
shared ``assert_public_http_url`` gate BEFORE any request is sent — otherwise
a logged-in user can probe the metadata endpoint and every docker service.

Contract under test:
  - a user-sourced base_url resolving to a private / metadata / docker-internal
    address is refused and httpx is never constructed;
  - a public base_url still passes (resolver monkeypatched: no real DNS);
  - platform-issued cards (netmind_free on our own gateway) are decided by the
    STORED row source and keep working with their internal base_url;
  - local mode keeps localhost endpoints (Ollama / LM Studio) allowed;
  - failure messages never echo the upstream body or exception text.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod
from narranexus.platform.agent_framework.providers.user_service import (
    UserProviderService,
)
from narranexus.platform.utils import url_safety
from tests.fake_db import FakeDB

UPSTREAM_SECRET = "UPSTREAM-SECRET-BODY-imds-role-creds"

# Hostname -> addresses, standing in for DNS.
_FAKE_DNS = {
    "proxy.example.com": ["93.184.216.34"],
    "llm-gateway": ["172.20.0.18"],
    "quota-api": ["172.20.0.7"],
}


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeHttpx:
    """Records every AsyncClient construction + request; scripted response."""

    def __init__(self):
        self.clients: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.response = _FakeResponse(200, "{}")
        self.raise_exc: Exception | None = None

    def factory(self, *args, **kwargs):
        recorder = self
        recorder.clients.append(kwargs)

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **kw):
                recorder.requests.append(("GET", url))
                if recorder.raise_exc:
                    raise recorder.raise_exc
                return recorder.response

            async def post(self, url, **kw):
                recorder.requests.append(("POST", url))
                if recorder.raise_exc:
                    raise recorder.raise_exc
                return recorder.response

        return _Client()


@pytest.fixture
def fake_http(monkeypatch):
    fake = _FakeHttpx()
    monkeypatch.setattr(httpx, "AsyncClient", fake.factory)

    async def _resolver(host, port):
        if host not in _FAKE_DNS:
            raise OSError(f"unknown host {host}")
        return _FAKE_DNS[host]

    monkeypatch.setattr(url_safety, "_default_resolver", _resolver)
    return fake


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")


def _probe(svc: UserProviderService, base_url: str):
    return svc.test_provider_config(
        card_type="openai",
        api_key="sk-test",
        base_url=base_url,
        auth_type="api_key",
        models=["m1"],
    )


# ── test-config (stateless form probe) ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://169.254.169.254/latest/meta-data",  # IMDS (link-local)
        "http://172.20.0.18:4000/v1",                # docker network literal
        "http://llm-gateway:4000/v1",                # docker service name
        "http://quota-api:8080",                     # docker service name
        "http://127.0.0.1:8000",                     # backend loopback
        "http://unresolvable-host.invalid/v1",       # DNS failure must reject
        "http://[::1",                               # malformed: fail closed
    ],
)
async def test_cloud_refuses_internal_base_url_before_any_request(cloud, fake_http, base_url):
    ok, msg = await _probe(UserProviderService(FakeDB()), base_url)

    assert ok is False
    assert "public" in msg.lower()
    assert fake_http.clients == []
    assert fake_http.requests == []
    # The resolved internal address is never echoed back.
    assert "172.20" not in msg and "169.254" not in msg


@pytest.mark.asyncio
async def test_cloud_public_base_url_still_probed(cloud, fake_http):
    ok, msg = await _probe(UserProviderService(FakeDB()), "https://proxy.example.com/v1")

    assert ok is True, msg
    assert fake_http.requests == [("POST", "https://proxy.example.com/v1/chat/completions")]
    assert all(c.get("follow_redirects") is False for c in fake_http.clients)


@pytest.mark.asyncio
async def test_local_mode_keeps_localhost_endpoint(local, fake_http):
    ok, _ = await _probe(UserProviderService(FakeDB()), "http://localhost:11434/v1")

    assert ok is True
    assert fake_http.requests == [("POST", "http://localhost:11434/v1/chat/completions")]


@pytest.mark.asyncio
async def test_upstream_error_body_is_not_echoed(cloud, fake_http):
    fake_http.response = _FakeResponse(500, UPSTREAM_SECRET)

    ok, msg = await _probe(UserProviderService(FakeDB()), "https://proxy.example.com/v1")

    assert ok is False
    assert "500" in msg
    assert UPSTREAM_SECRET not in msg


@pytest.mark.asyncio
async def test_official_endpoint_error_body_is_not_echoed(cloud, fake_http):
    fake_http.response = _FakeResponse(418, UPSTREAM_SECRET)

    ok, msg = await UserProviderService(FakeDB()).test_provider_config(
        card_type="openai", api_key="sk", base_url="", models=[],
    )

    assert ok is False
    assert "418" in msg
    assert UPSTREAM_SECRET not in msg


@pytest.mark.asyncio
async def test_exception_text_is_not_echoed(cloud, fake_http):
    fake_http.raise_exc = httpx.ConnectError(f"connect to 10.0.0.9 failed: {UPSTREAM_SECRET}")

    ok, msg = await _probe(UserProviderService(FakeDB()), "https://proxy.example.com/v1")

    assert ok is False
    assert UPSTREAM_SECRET not in msg
    assert "10.0.0.9" not in msg


def test_test_config_route_refuses_internal_host_end_to_end(cloud, fake_http, monkeypatch):
    """Route -> real service -> real registry: the JSON the user sees is generic."""

    async def _get_service():
        return UserProviderService(FakeDB())

    monkeypatch.setattr(providers_mod, "_get_service", _get_service)
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = "u1"
        return await call_next(request)

    app.include_router(providers_mod.router, prefix="/api/providers")
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/providers/test-config",
        json={"card_type": "openai", "api_key": "sk", "base_url": "http://quota-api:8080"},
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert "172.20" not in resp.text
    assert fake_http.requests == []


# ── saved-row test: decided by the STORED source ────────────────────────────


def _row(provider_id: str, source: str, base_url: str, auth_type: str = "api_key") -> dict:
    return {
        "user_id": "u1",
        "provider_id": provider_id,
        "name": provider_id,
        "source": source,
        "protocol": "openai",
        "auth_type": auth_type,
        "api_key": "sk-test",
        "base_url": base_url,
        "models": json.dumps(["m1"]),
    }


@pytest.mark.asyncio
async def test_platform_free_tier_row_on_internal_gateway_still_tests(cloud, fake_http):
    db = FakeDB()
    await db.insert("user_providers", _row("p_free", "netmind_free", "http://llm-gateway:4000/v1"))

    ok, msg = await UserProviderService(db).test_provider("u1", "p_free")

    assert ok is True, msg
    assert fake_http.requests == [("POST", "http://llm-gateway:4000/v1/chat/completions")]


@pytest.mark.asyncio
async def test_user_row_on_internal_gateway_is_refused(cloud, fake_http):
    db = FakeDB()
    await db.insert("user_providers", _row("p_user", "user", "http://llm-gateway:4000/v1"))

    ok, msg = await UserProviderService(db).test_provider("u1", "p_user")

    assert ok is False
    assert "public" in msg.lower()
    assert fake_http.requests == []


# ── save path: a stored custom card can't point at an internal host ─────────


@pytest.mark.asyncio
async def test_cloud_add_custom_provider_rejects_internal_base_url(cloud, fake_http):
    db = FakeDB()

    with pytest.raises(ValueError) as exc:
        await UserProviderService(db).add_provider(
            user_id="u1", card_type="openai", api_key="sk",
            base_url="http://172.20.0.18:4000/v1", models=["m1"],
        )

    assert "public" in str(exc.value).lower()
    assert "172.20" not in str(exc.value)
    assert db.tables["user_providers"] == []


@pytest.mark.asyncio
async def test_cloud_add_custom_provider_accepts_public_base_url(cloud, fake_http):
    db = FakeDB()

    _, new_ids = await UserProviderService(db).add_provider(
        user_id="u1", card_type="anthropic", api_key="sk",
        base_url="https://proxy.example.com", models=["m1"],
    )

    assert len(new_ids) == 1
    assert db.tables["user_providers"][0]["base_url"] == "https://proxy.example.com"


@pytest.mark.asyncio
async def test_local_add_custom_provider_keeps_localhost(local, fake_http):
    db = FakeDB()

    _, new_ids = await UserProviderService(db).add_provider(
        user_id="u1", card_type="openai", api_key="sk",
        base_url="http://localhost:11434/v1", models=["m1"],
    )

    assert len(new_ids) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["mystery-source", None, ""])
async def test_saved_row_with_unknown_or_missing_source_is_gated_as_user(cloud, fake_http, source):
    """Fail closed: a row whose source is unknown / missing / empty is treated
    as USER, so its base_url goes through the egress gate."""
    row = _row("p_odd", "user", "http://llm-gateway:4000/v1")
    if source is None:
        row.pop("source")
    else:
        row["source"] = source
    db = FakeDB()
    await db.insert("user_providers", row)

    ok, msg = await UserProviderService(db).test_provider("u1", "p_odd")

    assert ok is False
    assert "public" in msg.lower()
    assert fake_http.requests == []


# ── read path: a STORED user card on an internal host is unusable (cloud) ───
#
# Rows saved before the save-time gate existed (2026-10-28 incident) must not
# keep driving server-side requests. The read path uses the DNS-free screen
# (`is_obviously_non_public_url`) — no resolution on the agent-loop hot path.


def _slot(slot_name: str, provider_id: str, model: str = "m1") -> dict:
    return {"user_id": "u1", "slot_name": slot_name, "provider_id": provider_id, "model": model}


async def _seed_bound_row(source: str, protocol: str = "openai") -> FakeDB:
    db = FakeDB()
    row = _row("p1", source, "http://llm-gateway:4000/v1")
    row["protocol"] = protocol
    await db.insert("user_providers", row)
    await db.insert("user_slots", _slot("agent", "p1"))
    await db.insert("user_slots", _slot("helper_llm", "p1"))
    return db


@pytest.mark.asyncio
async def test_cloud_read_path_marks_stored_internal_user_card_unusable(cloud, fake_http):
    from narranexus.platform.agent_framework.providers.resolver import (
        is_user_config_complete,
    )

    db = await _seed_bound_row("user")
    cfg = await UserProviderService(db).get_user_config("u1")

    assert cfg.providers["p1"].is_active is False  # still listed, so it can be deleted
    assert is_user_config_complete(cfg) is False   # but never resolved for a run
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_local_read_path_keeps_stored_internal_user_card(local, fake_http):
    db = await _seed_bound_row("user")
    cfg = await UserProviderService(db).get_user_config("u1")

    assert cfg.providers["p1"].is_active is True


@pytest.mark.asyncio
async def test_cloud_read_path_keeps_platform_free_tier_card_on_internal_gateway(cloud, fake_http):
    from narranexus.platform.agent_framework.providers.resolver import (
        is_user_config_complete,
    )

    db = await _seed_bound_row("netmind_free")
    cfg = await UserProviderService(db).get_user_config("u1")

    assert cfg.providers["p1"].is_active is True
    assert is_user_config_complete(cfg) is True


@pytest.mark.asyncio
async def test_cloud_runtime_builder_refuses_stored_internal_user_card(cloud, fake_http):
    """The runtime builder reads user_providers by provider_id directly (and is
    reached through per-agent overrides that `get_user_config` never sees), so
    it must apply the same read-path screen."""
    from narranexus.platform.agent_framework.api_config import LLMConfigNotConfigured
    from narranexus.platform.agent_framework.providers.driver import (
        resolve_user_runtime_llm_configs,
    )

    db = await _seed_bound_row("user", protocol="anthropic")
    with pytest.raises(LLMConfigNotConfigured) as exc:
        await resolve_user_runtime_llm_configs("u1", db)
    assert "llm-gateway" not in str(exc.value)


@pytest.mark.asyncio
async def test_local_runtime_builder_keeps_stored_internal_user_card(local, fake_http):
    from narranexus.platform.agent_framework.providers.driver import (
        resolve_user_runtime_llm_configs,
    )

    db = await _seed_bound_row("user", protocol="anthropic")
    cfgs = await resolve_user_runtime_llm_configs("u1", db)
    assert cfgs.claude.base_url == "http://llm-gateway:4000/v1"


# ── platform sources outside the enum (cloud free-tier pool) ────────────────
#
# `user_providers.source` holds platform values the ProviderSource enum has no
# member for: `system` (derive.py → system_pool driver) and `system_pool`
# (model_sync.apply_ledger_to_db). Their base_url is our internal gateway, like
# netmind_free. "Not an enum member" must not mean "user" — only `user`,
# missing and genuinely unknown values are gated.

_INTERNAL_GATEWAY = "http://llm-gateway:4000/v1"
_PLATFORM_SOURCES = [
    "netmind", "netmind_free", "yunwu", "openrouter",
    "claude_oauth", "codex_oauth", "system", "system_pool",
]


@pytest.mark.parametrize("source", _PLATFORM_SOURCES)
def test_cloud_read_screen_trusts_every_platform_source(cloud, source):
    from narranexus.platform.agent_framework.providers.registry import (
        is_stored_user_base_url_blocked,
    )

    assert is_stored_user_base_url_blocked(source, _INTERNAL_GATEWAY, "p1") is False


@pytest.mark.parametrize("source", ["user", "totally_unknown", None, ""])
def test_cloud_read_screen_gates_user_missing_and_unknown_sources(cloud, source):
    from narranexus.platform.agent_framework.providers.registry import (
        is_stored_user_base_url_blocked,
    )

    assert is_stored_user_base_url_blocked(source, _INTERNAL_GATEWAY, "p1") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["system", "system_pool"])
async def test_cloud_runtime_builder_keeps_system_pool_card_on_internal_gateway(
    cloud, fake_http, monkeypatch, source
):
    """The agent-loop builder must keep serving the cloud free-tier pool card:
    refusing it would fail every run of every user bound to it."""
    from narranexus.platform.agent_framework.providers.driver import (
        resolve_user_runtime_llm_configs,
    )
    from narranexus.platform.agent_framework.providers.driver import resolver as resolver_mod
    from narranexus_plugins.providers.system import SystemDriver

    real_lookup = resolver_mod.get_driver_class
    monkeypatch.setattr(
        resolver_mod,
        "get_driver_class",
        lambda key: SystemDriver if key == "system_pool" else real_lookup(key),
    )
    db = FakeDB()
    row = _row("p1", source, _INTERNAL_GATEWAY)
    row.update(protocol="anthropic", auth_type="bearer_token", driver_type="system_pool")
    await db.insert("user_providers", row)
    await db.insert("user_slots", _slot("agent", "p1"))
    await db.insert("user_slots", _slot("helper_llm", "p1"))

    cfgs = await resolve_user_runtime_llm_configs("u1", db)

    assert cfgs.claude.base_url == _INTERNAL_GATEWAY
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_cloud_runtime_builder_still_refuses_unknown_source_card(cloud, fake_http):
    """Fail closed is not weakened: a genuinely unknown source is gated."""
    from narranexus.platform.agent_framework.api_config import LLMConfigNotConfigured
    from narranexus.platform.agent_framework.providers.driver import (
        resolve_user_runtime_llm_configs,
    )

    db = await _seed_bound_row("totally_unknown", protocol="anthropic")
    with pytest.raises(LLMConfigNotConfigured) as exc:
        await resolve_user_runtime_llm_configs("u1", db)
    assert "not allowed in the cloud" in str(exc.value)
    assert "llm-gateway" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["system", "system_pool"])
async def test_platform_system_pool_row_on_internal_gateway_still_tests(cloud, fake_http, source):
    """Saved-row Test (also ProviderReadiness) uses the same platform-source
    criterion as the read path, so the pool card is probed, not refused."""
    db = FakeDB()
    await db.insert("user_providers", _row("p_pool", source, _INTERNAL_GATEWAY))

    ok, msg = await UserProviderService(db).test_provider("u1", "p_pool")

    assert ok is True, msg
    assert fake_http.requests == [("POST", f"{_INTERNAL_GATEWAY}/chat/completions")]
