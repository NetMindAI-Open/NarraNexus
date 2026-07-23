"""
@file_name: test_agent_framework_switch_gate.py
@author:
@date: 2026-07-16
@description: POST /api/providers/agent-framework — the cloud 403 gate must be
DIRECTION-AWARE: a non-staff cloud user may switch back TO claude_code (the fix
for the old-codex lockout), but not TO another framework.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod

USER = {"X-User-Id": "u1"}
STAFF = {"X-User-Id": "u1", "X-Role": "staff"}


@pytest.fixture
def make_client(monkeypatch):
    def _make(*, cloud: bool):
        # Hermetic: set the mode explicitly and clear the higher-priority
        # NARRANEXUS_DEPLOYMENT_MODE so an inherited env var can't flip the
        # deployment-mode inference under the test.
        from xyz_agent_context.utils.deployment_mode import DEPLOYMENT_MODE_ENV_VAR
        monkeypatch.delenv(DEPLOYMENT_MODE_ENV_VAR, raising=False)
        monkeypatch.setenv(
            "DATABASE_URL",
            "mysql://u:p@h/db" if cloud else "sqlite:///local.db",
        )
        # Past the gate → service reached → raise a marker → 400 (distinguishes
        # "gate passed" from the gate's own 403).
        class _Stub:
            async def set_user_agent_framework(self, *_a, **_kw):
                raise ValueError("STUB_REACHED")

        async def _get_service():
            return _Stub()
        monkeypatch.setattr(providers_mod, "_get_service", _get_service)

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


def _post(client, framework, headers):
    return client.post("/api/providers/agent-framework",
                       json={"framework": framework}, headers=headers)


def test_cloud_nonstaff_can_switch_to_claude_code(make_client):
    # THE FIX: → claude_code must pass the gate (previously 403'd unconditionally).
    resp = _post(make_client(cloud=True), "claude_code", USER)
    assert resp.status_code != 403


def test_cloud_nonstaff_blocked_switching_to_codex(make_client):
    resp = _post(make_client(cloud=True), "codex_cli", USER)
    assert resp.status_code == 403


def test_local_nonstaff_not_gated(make_client):
    # Local mode is unrestricted → not the 403 gate (reaches codex path/service).
    resp = _post(make_client(cloud=False), "claude_code", USER)
    assert resp.status_code != 403
