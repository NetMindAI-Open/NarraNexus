"""
@file_name: test_telemetry_consent_routes.py
@date: 2026-08-11
@description: GET/PUT telemetry consent (diagnostic log shipping) via the API.

The consent STATE lives in a per-machine marker file
(~/.narranexus/telemetry_optout, read by utils/logging/_ship at every
send), not in the DB — logging starts before the DB does. That makes the
write single-tenant-only: on a multi-tenant cloud install one user must
not be able to silence (or re-enable) telemetry for everyone, so PUT is
403 in cloud mode and GET reports controllable=false. An explicit
NEXUS_DIAG_SHIP env override is the deployment's decision: GET reports
source=env / controllable=false and PUT is 409.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.utils.logging import _ship


async def _async_return(v):
    return v


@pytest.fixture
def client(db_client, monkeypatch, tmp_path):
    import backend.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: False)
    # Keep the suite away from the developer's real marker file, and the
    # suite's own kill switch (conftest sets NEXUS_DIAG_SHIP=off) away
    # from consent resolution.
    monkeypatch.setattr(_ship, "_OPTOUT_FILE", tmp_path / "telemetry_optout")
    monkeypatch.delenv("NEXUS_DIAG_SHIP", raising=False)
    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


H = {"X-User-Id": "u1"}


def test_default_state_is_on_and_controllable(client):
    g = client.get("/api/auth/settings/telemetry", headers=H)
    assert g.status_code == 200
    body = g.json()
    assert body == {
        "mode": "meta",
        "source": "default",
        "opted_out": False,
        "controllable": True,
        "managed_by": None,
    }


def test_opt_out_roundtrip_writes_marker_file(client):
    p = client.put("/api/auth/settings/telemetry",
                   json={"opted_out": True}, headers=H)
    assert p.status_code == 200 and p.json()["opted_out"] is True
    assert _ship._OPTOUT_FILE.exists()
    g = client.get("/api/auth/settings/telemetry", headers=H)
    assert g.json()["mode"] == "off"
    assert g.json()["source"] == "optout"
    assert g.json()["opted_out"] is True
    p2 = client.put("/api/auth/settings/telemetry",
                    json={"opted_out": False}, headers=H)
    assert p2.status_code == 200
    assert not _ship._OPTOUT_FILE.exists()


def test_cloud_mode_hides_the_knob_and_refuses_writes(client, monkeypatch):
    import backend.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    g = client.get("/api/auth/settings/telemetry", headers=H)
    assert g.json()["controllable"] is False
    # "cloud", not "env": attributing the built-in default to an env
    # var nobody set would tell the user their deployment decided.
    assert g.json()["managed_by"] == "cloud"
    p = client.put("/api/auth/settings/telemetry",
                   json={"opted_out": True}, headers=H)
    assert p.status_code == 403
    assert not _ship._OPTOUT_FILE.exists()


def test_cloud_deployment_without_opt_in_reports_off(client, monkeypatch):
    """A cloud stack that set neither NEXUS_DIAG_SHIP nor the managed
    default ships nothing, and the settings page says so — the built-in
    meta default belongs to single-tenant installs with a live toggle."""
    import backend.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    g = client.get("/api/auth/settings/telemetry", headers=H)
    assert g.json() == {
        "mode": "off",
        "source": "default",
        "opted_out": False,
        "controllable": False,
        "managed_by": "cloud",
    }


def test_env_override_reports_env_source_and_refuses_writes(client, monkeypatch):
    monkeypatch.setenv("NEXUS_DIAG_SHIP", "meta")
    g = client.get("/api/auth/settings/telemetry", headers=H)
    assert g.json() == {
        "mode": "meta",
        "source": "env",
        "opted_out": False,
        "controllable": False,
        "managed_by": "env",
    }
    p = client.put("/api/auth/settings/telemetry",
                   json={"opted_out": True}, headers=H)
    assert p.status_code == 409
    assert not _ship._OPTOUT_FILE.exists()


def test_requires_identity(client):
    assert client.get("/api/auth/settings/telemetry").status_code == 401
    assert client.put("/api/auth/settings/telemetry",
                      json={"opted_out": True}).status_code == 401
