"""
@file_name: test_cloud_netmind_only_slots.py
@author: NarraNexus
@date: 2026-07-17
@description: Cloud netmind-only slot policy on the providers routes.

Cloud accounts run on the user's NetMind ("Power") account: a non-staff
cloud user may not bind a bring-your-own provider (source != "netmind")
to a slot — own API keys are a local/desktop-version feature. Staff and
local deployments are exempt. The rule lives in
``agent_framework/cloud_policy.py`` and is enforced INSIDE
``UserProviderService.set_slot`` (``actor_is_staff`` param, raising
``CloudPolicyViolation`` which the route maps to 403).

Covered here:
  - ``PUT /api/providers/slots/{slot}`` end-to-end against a real
    in-memory DB + real ``UserProviderService`` (the policy check moved
    into the service, so a stubbed service would test nothing).
  - ``POST /api/providers/onboard`` → register-only (``activate=False``)
    on cloud non-staff; staff and local keep ``activate=True`` (stubbed
    service — this is route-level behavior).
  - ``POST /api/providers`` (add) → the optional ``default_slots``
    assignment is skipped on cloud non-staff (register-only semantics).

The per-agent writer's policy tests (same cloud_policy rules through
``AgentSlotService``) live in test_agents_llm_config_routes.py.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.schema_registry import auto_migrate

USER = {"X-User-Id": "u1"}
STAFF = {"X-User-Id": "u1", "X-Role": "staff"}


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


@pytest.fixture
def make_client(monkeypatch):
    """TestClient with fake auth middleware and an explicit deployment mode.

    ``X-Role`` header → ``request.state.role`` (mirrors auth_middleware);
    cloud/local is signalled via NARRANEXUS_DEPLOYMENT_MODE (the SSOT env).
    """

    def _make(*, cloud: bool):
        monkeypatch.setenv(
            "NARRANEXUS_DEPLOYMENT_MODE", "cloud" if cloud else "local"
        )
        app = FastAPI()

        @app.middleware("http")
        async def fake_auth(request: Request, call_next):
            request.state.user_id = request.headers.get("X-User-Id") or None
            role = request.headers.get("X-Role")
            if role:
                request.state.role = role
            return await call_next(request)

        app.include_router(providers_mod.router, prefix="/api/providers")
        return TestClient(app, raise_server_exceptions=False)

    return _make


def _wire_real_service(monkeypatch, db_client):
    """Route → a REAL UserProviderService over the in-memory DB, with the
    route's post-write side effects (job rearm, circuit-breaker resume)
    stubbed out — they need the global runtime."""
    from xyz_agent_context.agent_framework.user_provider_service import (
        UserProviderService,
    )

    async def _get_service():
        return UserProviderService(db_client)

    monkeypatch.setattr(providers_mod, "_get_service", _get_service)

    async def _noop_resume(_uid):
        return None

    monkeypatch.setattr(providers_mod, "_resume_agent_circuit_breakers", _noop_resume)
    import xyz_agent_context.module.job_module.job_recovery as job_recovery_mod

    monkeypatch.setattr(
        job_recovery_mod, "schedule_user_no_quota_rearm", lambda _uid: None
    )


async def _seed_provider(
    db_client, provider_id, *, source="user", protocol="anthropic"
):
    """Full row — the success path round-trips it through ProviderConfig."""
    await db_client.insert(
        "user_providers",
        {
            "provider_id": provider_id,
            "user_id": "u1",
            "name": provider_id,
            "source": source,
            "protocol": protocol,
            "auth_type": "api_key",
            "api_key": "sk-test-1234",
            "base_url": "",
            "models": json.dumps(["model-a"]),
            "linked_group": "",
            "is_active": 1,
        },
    )


# =============================================================================
# PUT /api/providers/slots/{slot} — real service, policy enforced in set_slot
# =============================================================================


@pytest.mark.asyncio
async def test_slots_cloud_nonstaff_rejects_own_key_provider(
    make_client, monkeypatch, db_client
):
    """Cloud + non-staff may not bind a bring-your-own (source="user")
    provider — 403, nothing written."""
    _wire_real_service(monkeypatch, db_client)
    await _seed_provider(db_client, "p1", source="user")
    client = make_client(cloud=True)

    r = client.put(
        "/api/providers/slots/agent",
        json={"provider_id": "p1", "model": "model-a"},
        headers=USER,
    )
    assert r.status_code == 403
    assert "NetMind" in r.json()["detail"]
    assert await db_client.get("user_slots", {"user_id": "u1"}) == []


@pytest.mark.asyncio
async def test_slots_cloud_nonstaff_accepts_netmind_provider(
    make_client, monkeypatch, db_client
):
    """A netmind-source provider binds normally for everyone on cloud."""
    _wire_real_service(monkeypatch, db_client)
    await _seed_provider(db_client, "p_nm", source="netmind")
    client = make_client(cloud=True)

    r = client.put(
        "/api/providers/slots/agent",
        json={"provider_id": "p_nm", "model": "model-a"},
        headers=USER,
    )
    assert r.status_code == 200
    rows = await db_client.get("user_slots", {"user_id": "u1"})
    assert len(rows) == 1 and rows[0]["provider_id"] == "p_nm"


@pytest.mark.asyncio
async def test_slots_cloud_staff_bypasses_gate(make_client, monkeypatch, db_client):
    """Staff keeps full provider choice on cloud (same exemption as the
    framework-switch and OAuth gates)."""
    _wire_real_service(monkeypatch, db_client)
    await _seed_provider(db_client, "p1", source="user")
    client = make_client(cloud=True)

    r = client.put(
        "/api/providers/slots/agent",
        json={"provider_id": "p1", "model": "model-a"},
        headers=STAFF,
    )
    assert r.status_code == 200
    rows = await db_client.get("user_slots", {"user_id": "u1"})
    assert len(rows) == 1 and rows[0]["provider_id"] == "p1"


@pytest.mark.asyncio
async def test_slots_local_stays_open(make_client, monkeypatch, db_client):
    """Local deployments keep bring-your-own-key fully self-serve."""
    _wire_real_service(monkeypatch, db_client)
    await _seed_provider(db_client, "p1", source="user")
    client = make_client(cloud=False)

    r = client.put(
        "/api/providers/slots/agent",
        json={"provider_id": "p1", "model": "model-a"},
        headers=USER,
    )
    assert r.status_code == 200
    rows = await db_client.get("user_slots", {"user_id": "u1"})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_slots_cloud_unknown_provider_is_not_found(
    make_client, monkeypatch, db_client
):
    """Missing provider row → the writer's own not-found (400), not a 403 —
    the policy check must not mask not-found."""
    _wire_real_service(monkeypatch, db_client)
    client = make_client(cloud=True)

    r = client.put(
        "/api/providers/slots/agent",
        json={"provider_id": "missing", "model": "model-a"},
        headers=USER,
    )
    assert r.status_code == 400
    assert "not found" in r.json()["detail"]


# =============================================================================
# POST /api/providers/onboard — register-only on cloud non-staff (route-level)
# =============================================================================


def _stub_onboard_service(monkeypatch, recorded: dict):
    class _Stub:
        async def onboard_one_key(self, *_a, **kw):
            recorded.update(kw)
            raise ValueError("STUB_REACHED")

    async def _get_service():
        return _Stub()

    monkeypatch.setattr(providers_mod, "_get_service", _get_service)


def test_onboard_cloud_nonstaff_is_register_only(make_client, monkeypatch):
    recorded: dict = {}
    _stub_onboard_service(monkeypatch, recorded)
    client = make_client(cloud=True)

    r = client.post(
        "/api/providers/onboard",
        json={"api_key": "sk-ant-test"},
        headers=USER,
    )
    assert r.status_code == 400  # stub marker — the call itself went through
    assert recorded.get("activate") is False


def test_onboard_cloud_staff_keeps_activate(make_client, monkeypatch):
    recorded: dict = {}
    _stub_onboard_service(monkeypatch, recorded)
    client = make_client(cloud=True)

    client.post(
        "/api/providers/onboard",
        json={"api_key": "sk-ant-test"},
        headers=STAFF,
    )
    assert recorded.get("activate") is True


def test_onboard_local_keeps_activate(make_client, monkeypatch):
    recorded: dict = {}
    _stub_onboard_service(monkeypatch, recorded)
    client = make_client(cloud=False)

    client.post(
        "/api/providers/onboard",
        json={"api_key": "sk-ant-test"},
        headers=USER,
    )
    assert recorded.get("activate") is True


# =============================================================================
# POST /api/providers — default_slots skipped on cloud non-staff (route-level)
# =============================================================================


def _stub_add_service(monkeypatch):
    """Stub whose add_provider returns a config containing the new provider
    (so the default_slots loop can match it); set_slot records calls so the
    test can assert the loop was (not) entered. The route's post-write side
    effects (job rearm, circuit-breaker resume) are stubbed out — they need
    a real DB."""
    from xyz_agent_context.schema.provider_schema import (
        AuthType,
        LLMConfig,
        ProviderConfig,
        ProviderProtocol,
        ProviderSource,
    )

    cfg = LLMConfig(
        providers={
            "p_new": ProviderConfig(
                provider_id="p_new",
                name="Test",
                source=ProviderSource.USER,
                protocol=ProviderProtocol.ANTHROPIC,
                auth_type=AuthType.API_KEY,
            )
        }
    )
    calls: list[tuple] = []

    class _Stub:
        async def add_provider(self, **_kw):
            return cfg, ["p_new"]

        async def set_slot(self, *a, **kw):
            calls.append((a, kw))
            return cfg

    async def _get_service():
        return _Stub()

    monkeypatch.setattr(providers_mod, "_get_service", _get_service)

    async def _noop_resume(_uid):
        return None

    monkeypatch.setattr(providers_mod, "_resume_agent_circuit_breakers", _noop_resume)
    import xyz_agent_context.module.job_module.job_recovery as job_recovery_mod

    monkeypatch.setattr(
        job_recovery_mod, "schedule_user_no_quota_rearm", lambda _uid: None
    )
    return calls


def test_add_provider_cloud_nonstaff_skips_default_slots(make_client, monkeypatch):
    calls = _stub_add_service(monkeypatch)
    client = make_client(cloud=True)

    r = client.post(
        "/api/providers",
        json={
            "card_type": "anthropic",
            "api_key": "sk-ant-test",
            "default_slots": {
                "agent": {"protocol": "anthropic", "model": "claude"},
            },
        },
        headers=USER,
    )
    assert r.status_code == 200
    assert calls == []


def test_add_provider_local_honors_default_slots(make_client, monkeypatch):
    calls = _stub_add_service(monkeypatch)
    client = make_client(cloud=False)

    r = client.post(
        "/api/providers",
        json={
            "card_type": "anthropic",
            "api_key": "sk-ant-test",
            "default_slots": {
                "agent": {"protocol": "anthropic", "model": "claude"},
            },
        },
        headers=USER,
    )
    assert r.status_code == 200
    assert len(calls) == 1  # default_slots honored: agent slot was assigned
