"""
@file_name: test_uninstalled_framework_binding_is_400.py
@author: Bin Liang
@date: 2026-09-07
@description: A ``turn.pipeline.act.framework`` binding naming a framework no plugin provides answers 400 on the provider routes, not 500 — the page that fixes the binding must stay renderable.

Round-2 P2-I2: making a misbinding loud landed
(``bound_default_framework`` raises ``FrameworkNotInstalledError``, a
``RuntimeError``), but the conversion at the schema/policy boundary did not.
``get_slot_required_protocols`` / ``framework_can_drive_provider`` are pure
predicates consumed by ``validate_slot_binding`` and the provider routes, which
map ``ValueError`` → 400 and have no handler for ``RuntimeError`` — so a user
who uninstalled a bound framework (or a distribution that excludes one while
``narranexus.toml`` still binds it) got a 500 on the whole provider page, i.e.
the one recovery path from a bad binding was the page the bad binding broke.

Delete the ``except FrameworkNotInstalledError`` in
``providers/framework_binding._resolved_meta`` and
``test_the_route_answers_400_not_500`` goes red (500), while
``test_the_turn_path_keeps_the_raw_error`` guards the other direction: the
conversion must NOT leak onto the turn, where the raw message is the actionable
one, and must NOT widen into "unknown framework → default".
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod
from narranexus.platform.agent_framework.loop import driver as driver_mod
from narranexus.platform.agent_framework.providers.framework_binding import (
    framework_can_drive_provider,
    get_slot_required_protocols,
)
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

USER = {"X-User-Id": "u1"}


@pytest.fixture
def misbound(monkeypatch):
    """The ``turn.pipeline.act.framework`` binding names a plugin that is not
    registered in this process — the exact state a desktop uninstall leaves."""

    def _boom() -> str:
        raise driver_mod.FrameworkNotInstalledError("acme_turbo")

    monkeypatch.setattr(driver_mod, "bound_default_framework", _boom)


def test_the_predicates_convert_to_value_error(misbound):
    with pytest.raises(ValueError) as exc:
        get_slot_required_protocols("agent")
    assert "acme_turbo" in str(exc.value)
    assert not isinstance(exc.value, driver_mod.FrameworkNotInstalledError)

    with pytest.raises(ValueError):
        framework_can_drive_provider(None, source="user", auth_type="api_key", protocol="anthropic")


def test_an_unknown_named_framework_is_still_refused_not_defaulted():
    """The conversion must not widen into a silent fallback (round-1 I4)."""
    with pytest.raises(ValueError, match="ghost"):
        get_slot_required_protocols("agent", agent_framework="ghost")


def test_the_turn_path_keeps_the_raw_error(misbound):
    """``get_agent_loop_driver`` is where the message is actionable, so it must
    stay ``FrameworkNotInstalledError`` there."""
    with pytest.raises(driver_mod.FrameworkNotInstalledError):
        driver_mod.resolve_framework_name(None)


def test_the_picker_can_still_list_an_uninstalled_framework_as_disabled():
    """``framework_installed`` must keep answering False rather than raising —
    the selector greys the entry out instead of 500-ing the page."""
    assert driver_mod.framework_installed("ghost") is False


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


@pytest.fixture
def client(monkeypatch, db_client):
    from narranexus.platform.agent_framework.providers.user_service import (
        UserProviderService,
    )

    async def _get_service():
        return UserProviderService(db_client)

    monkeypatch.setattr(providers_mod, "_get_service", _get_service)

    async def _noop_resume(_uid):
        return None

    monkeypatch.setattr(providers_mod, "_resume_agent_circuit_breakers", _noop_resume)
    import narranexus_plugins.job_module.job_recovery as job_recovery_mod

    monkeypatch.setattr(job_recovery_mod, "schedule_user_no_quota_rearm", lambda _uid: None)

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(providers_mod.router, prefix="/api/providers")
    return TestClient(app, raise_server_exceptions=False)


async def _seed(db_client):
    await db_client.insert(
        "user_providers",
        {
            "provider_id": "p1", "user_id": "u1", "name": "p1", "source": "user",
            "protocol": "anthropic", "auth_type": "api_key", "api_key": "sk-test-1234",
            "base_url": "", "models": json.dumps(["model-a"]), "linked_group": "", "is_active": 1,
        },
    )


@pytest.mark.asyncio
async def test_the_route_answers_400_not_500(db_client, client, misbound):
    """Through the REAL route layer: the binding is refused with an actionable
    400, which is what keeps the settings page usable."""
    await _seed(db_client)
    resp = client.put(
        "/api/providers/slots/agent",
        json={"provider_id": "p1", "model": "model-a"},
        headers=USER,
    )
    assert resp.status_code == 400, resp.text
    assert "acme_turbo" in resp.text
