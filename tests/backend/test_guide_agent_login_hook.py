"""
@file_name: test_guide_agent_login_hook.py
@author: Bin Liang
@date: 2026-08-19
@description: The login-path hooks for onboarding guide-agent provisioning.
Pins: all three entry points schedule it (netmind-login on EVERY login — the
zero-agent-existing-user pickup rides that; local login; local create-user),
the env kill-switch schedules nothing, a suspended account never reaches the
hook, and a crashing provisioning task cannot fail the login response.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.integrations.netmind.netmind_auth_client import NetmindUser

_CODE = "g" * 32


class _FakeNetmind:
    def __init__(self, user):
        self.user = user

    async def verify_token(self, token):
        return self.user


@pytest.fixture
def guide_spy(monkeypatch):
    """Enable the feature (conftest force-disables it suite-wide) and replace
    ensure_guide_agent with a recorder."""
    import backend.onboarding.provisioning as ob_pkg

    monkeypatch.setenv("NARRANEXUS_ONBOARDING_GUIDE_AGENT", "1")
    calls: list[tuple[str, bool]] = []

    async def _fake_ensure(db, user_id, *, is_new_user=False):
        calls.append((user_id, is_new_user))
        return {"provisioned": True}

    monkeypatch.setattr(ob_pkg, "ensure_guide_agent", _fake_ensure)
    return calls


def _make_app(db_client, monkeypatch, *, netmind_user=None, cloud=True):
    import backend.routes.auth as auth_mod
    import backend.integrations.free_tier.provisioner as prov_mod
    import backend.integrations.netmind.netmind_provisioner as nm_prov_mod

    async def _async_return(value):
        return value

    async def _noop_prov(user_id, *a, **kw):
        return None

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: True)
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: cloud)
    # Keep the OTHER fire-and-forget login tasks inert (network-free).
    monkeypatch.setattr(prov_mod, "ensure_free_tier_provider", _noop_prov)
    monkeypatch.setattr(nm_prov_mod, "ensure_netmind_provider", _noop_prov)
    if netmind_user is not None:
        monkeypatch.setattr(
            auth_mod, "_get_netmind_auth_client", lambda: _FakeNetmind(netmind_user)
        )

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


_USER = NetmindUser(user_system_code=_CODE, email="g@x.y", nickname="G")


def test_netmind_login_schedules_guide_agent_every_login(db_client, monkeypatch, guide_spy):
    client = _make_app(db_client, monkeypatch, netmind_user=_USER)
    with client:  # context manager keeps the loop alive for background tasks
        first = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})
        second = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})
    assert first.status_code == 200 and second.status_code == 200
    # EVERY login schedules it (not just is_new): that is what lets an
    # existing zero-agent user pick up their guide on a later login. The
    # is_new flag rides along so the backfill brake can tell them apart.
    assert guide_spy == [(_CODE, True), (_CODE, False)]
    # The response echoes the server switch — the frontend coachmark's gate.
    assert first.json()["guide_agent_provisioning"] is True


def test_kill_switch_schedules_nothing(db_client, monkeypatch, guide_spy):
    monkeypatch.setenv("NARRANEXUS_ONBOARDING_GUIDE_AGENT", "0")
    client = _make_app(db_client, monkeypatch, netmind_user=_USER)
    with client:
        resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})
    assert resp.status_code == 200
    assert guide_spy == []
    # The kill-switch reaches the frontend too: with it off, the response
    # must NOT tell the UI a guide agent is coming (else the coachmark
    # promises an agent that never appears).
    assert resp.json()["guide_agent_provisioning"] is False


@pytest.mark.asyncio
async def test_suspended_account_never_reaches_the_hook(db_client, monkeypatch, guide_spy):
    from narranexus.platform.repository.user_repository import UserRepository

    repo = UserRepository(db_client)
    await repo.add_user(user_id=_CODE, user_type="individual", display_name="G")
    await db_client.execute(
        "UPDATE users SET status = %s WHERE user_id = %s",
        params=("banned", _CODE),
        fetch=False,
    )
    client = _make_app(db_client, monkeypatch, netmind_user=_USER)
    with client:
        resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})
    assert resp.status_code == 403
    assert guide_spy == []


def test_local_login_schedules_guide_agent(db_client, monkeypatch, guide_spy):
    import asyncio

    from narranexus.platform.repository.user_repository import UserRepository

    asyncio.run(
        UserRepository(db_client).add_user(
            user_id="local_u", user_type="local", display_name="L"
        )
    )
    client = _make_app(db_client, monkeypatch, cloud=False)
    with client:
        resp = client.post("/api/auth/login", json={"user_id": "local_u"})
    assert resp.status_code == 200 and resp.json()["success"] is True
    assert guide_spy == [("local_u", False)]


def test_local_create_user_schedules_guide_agent(db_client, monkeypatch, guide_spy):
    client = _make_app(db_client, monkeypatch, cloud=False)
    with client:
        resp = client.post(
            "/api/auth/create-user", json={"user_id": "fresh_u", "display_name": "F"}
        )
    assert resp.status_code == 200 and resp.json()["success"] is True
    assert guide_spy == [("fresh_u", True)]


def test_crashing_provisioning_cannot_fail_the_login(db_client, monkeypatch):
    import backend.onboarding.provisioning as ob_pkg

    monkeypatch.setenv("NARRANEXUS_ONBOARDING_GUIDE_AGENT", "1")

    async def _boom(db, user_id, *, is_new_user=False):
        raise RuntimeError("provisioning exploded")

    monkeypatch.setattr(ob_pkg, "ensure_guide_agent", _boom)
    client = _make_app(db_client, monkeypatch, netmind_user=_USER)
    with client:
        resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})
    assert resp.status_code == 200 and resp.json()["success"] is True
