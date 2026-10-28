"""
@file_name: test_providers_circuit_breaker_resume.py
@author:
@date: 2026-07-13
@description: providers route auto-resume wrapper delegates to the breaker and
never fails the reconfigure.
"""
from unittest.mock import MagicMock

import pytest

import backend.routes.providers as prov_routes
import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from backend.routes.providers import _resume_agent_circuit_breakers


def _mock_request(user_id: str = "u1") -> MagicMock:
    req = MagicMock()
    req.state.user_id = user_id
    req.state.role = "user"
    req.query_params = {}
    return req


@pytest.mark.asyncio
async def test_resume_delegates_to_reset_for_owner(monkeypatch):
    seen = []

    async def fake_reset(user_id, db=None, *, provider_id=None):
        seen.append((user_id, provider_id))
        return 1
    monkeypatch.setattr(cb, "reset_for_owner", fake_reset)

    await _resume_agent_circuit_breakers("alice")
    await _resume_agent_circuit_breakers("alice", provider_id="p1")
    assert seen == [("alice", None), ("alice", "p1")]


@pytest.mark.asyncio
async def test_resume_swallows_errors(monkeypatch):
    async def boom(user_id, db=None, *, provider_id=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(cb, "reset_for_owner", boom)

    # Must NOT raise — provider reconfigure must never fail on breaker resume.
    await _resume_agent_circuit_breakers("bob")


# --------------------------------------------------------------------------
# GitHub #117: an EXPLICIT provider test that succeeds resumes the agents
# bound to that provider. The read-only status endpoints never resume.
# --------------------------------------------------------------------------

def _resume_spy(monkeypatch):
    calls = []

    async def fake_resume(uid, provider_id=None):
        calls.append((uid, provider_id))
    monkeypatch.setattr(prov_routes, "_resume_agent_circuit_breakers", fake_resume)
    return calls


@pytest.mark.asyncio
async def test_provider_test_success_resumes_breakers_for_that_provider(monkeypatch):
    calls = _resume_spy(monkeypatch)

    class FakeService:
        async def test_provider(self, uid, provider_id):
            return True, "ok"

    async def fake_get_service():
        return FakeService()
    monkeypatch.setattr(prov_routes, "_get_service", fake_get_service)

    resp = await prov_routes.test_provider("p1", _mock_request("alice"))
    assert resp["success"] is True
    assert calls == [("alice", "p1")]  # scoped, not the whole fleet


@pytest.mark.asyncio
async def test_provider_test_failure_does_not_resume_breakers(monkeypatch):
    calls = _resume_spy(monkeypatch)

    class FakeService:
        async def test_provider(self, uid, provider_id):
            return False, "dead key"

    async def fake_get_service():
        return FakeService()
    monkeypatch.setattr(prov_routes, "_get_service", fake_get_service)

    resp = await prov_routes.test_provider("p1", _mock_request("alice"))
    assert resp["success"] is False
    assert calls == []  # a still-broken provider must not resume anything


@pytest.mark.asyncio
async def test_claude_status_is_read_only_even_when_logged_in(monkeypatch):
    """GET /claude-status reports the HOST CLI; it must not touch the
    breaker — opening the provider picker used to un-pause every agent."""
    calls = _resume_spy(monkeypatch)
    monkeypatch.setattr(prov_routes, "_is_cloud", lambda: False)

    async def fake_probe(args, timeout):
        return {"loggedIn": True, "email": "alice@example.com"}
    monkeypatch.setattr(prov_routes, "_run_json_subprocess", fake_probe)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("shutil.which", lambda x: "/usr/bin/claude" if x == "claude" else None)
        resp = await prov_routes.get_claude_status(_mock_request("alice"))

    assert resp["data"]["logged_in"] is True
    assert calls == []


@pytest.mark.asyncio
async def test_codex_status_is_read_only_even_when_logged_in(tmp_path, monkeypatch):
    calls = _resume_spy(monkeypatch)
    monkeypatch.setattr(prov_routes, "_is_cloud", lambda: False)

    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("shutil.which", lambda x: "/usr/bin/codex" if x == "codex" else None)
        resp = await prov_routes.get_codex_status(_mock_request("alice"))

    assert resp["data"]["logged_in"] is True
    assert calls == []
