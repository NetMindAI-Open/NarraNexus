"""
@file_name: test_narramessenger_prewarm.py
@date: 2026-08-11
@description: Prewarm endpoints — bearer auth, agent resolution, broker seam.
"""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

import narranexus_plugins.narramessenger_module.routes as nm


@pytest.fixture(autouse=True)
def _stub_stale_replace_verdict(monkeypatch):
    """_do_prewarm now asks whether a stale image may be rolled, which reads
    the events table. Stubbed so these tests keep exercising the prewarm
    ledger rather than the DB.

    Defaults to the SAFE answer (False). Tests about ledger behaviour should
    not all run on the permissive path just because that is the interesting
    one for two other tests; those two override this."""
    async def verdict(user_id, *, active_run_id=None):
        return False

    monkeypatch.setattr(nm, "no_live_recorded_run_for", verdict)


class _FakeCred:
    agent_id = "agent_x"
    bearer_token = "secret-tok"
    enabled = True


class _FakeDisabledCred(_FakeCred):
    enabled = False


@pytest.fixture
def app(monkeypatch):
    async def fake_get_db():
        return object()
    monkeypatch.setattr(nm, "_get_db", fake_get_db)

    async def fake_by_mxid(self, mxid):
        return _FakeCred() if mxid == "@agent-x:hs" else None
    monkeypatch.setattr(
        nm.NarramessengerCredentialManager, "get_by_matrix_user_id", fake_by_mxid
    )

    async def fake_owner(self, agent_id):
        return "user_1"
    monkeypatch.setattr(nm.AgentRepository, "resolve_owner", fake_owner)

    monkeypatch.setattr(nm, "broker_url", lambda: "http://broker:8030")
    ensured = []

    async def fake_ensure(user_id, **kw):
        ensured.append(user_id)
        return nm.ExecutorEnsureResult(url="http://nx-exec-user-1:8020", cold_started=True)
    monkeypatch.setattr(nm, "ensure_executor", fake_ensure)

    async def fake_wait(url, **kw):
        return None
    monkeypatch.setattr(nm, "wait_until_ready", fake_wait)
    nm._PREWARM_STATE.clear()

    a = FastAPI()
    a.include_router(nm.router, prefix="/api/narramessenger")
    a.state.ensured = ensured
    yield a
    nm._PREWARM_STATE.clear()


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


BODY = {"agent_matrix_user_id": "@agent-x:hs", "rtc_session_id": "rtc-1"}
AUTH = {"Authorization": "Bearer secret-tok"}


@pytest.mark.asyncio
async def test_prewarm_happy_path_202_and_ensures_owner(app):
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r.status_code == 202
    assert r.json()["status"] == "warming"
    await asyncio.sleep(0)
    assert app.state.ensured == ["user_1"]


@pytest.mark.asyncio
async def test_prewarm_missing_bearer_401(app):
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY)
    assert r.status_code == 401
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_wrong_bearer_403_and_no_ensure(app):
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY,
                         headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_unknown_agent_404(app):
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm",
                         json={"agent_matrix_user_id": "@ghost:hs"}, headers=AUTH)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_prewarm_disabled_binding_404_and_no_ensure(app, monkeypatch):
    async def fake_disabled(self, mxid):
        return _FakeDisabledCred() if mxid == "@agent-x:hs" else None
    monkeypatch.setattr(
        nm.NarramessengerCredentialManager, "get_by_matrix_user_id", fake_disabled
    )
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r.status_code == 404
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_profile_id_path_202_and_ensures_owner(app, monkeypatch):
    async def fake_by_profile(self, profile_id):
        return _FakeCred() if profile_id == "prof-x" else None
    monkeypatch.setattr(
        nm.NarramessengerCredentialManager, "get_by_profile_id", fake_by_profile
    )
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm",
                         json={"agent_profile_id": "prof-x"}, headers=AUTH)
    assert r.status_code == 202
    assert r.json()["status"] == "warming"
    await asyncio.sleep(0)
    assert app.state.ensured == ["user_1"]


@pytest.mark.asyncio
async def test_prewarm_identifier_misuse_422(app):
    async with _client(app) as c:
        r0 = await c.post("/api/narramessenger/prewarm", json={}, headers=AUTH)
        r2 = await c.post("/api/narramessenger/prewarm",
                          json={"agent_matrix_user_id": "@agent-x:hs",
                                "agent_profile_id": "p1"}, headers=AUTH)
    assert r0.status_code == 422
    assert r2.status_code == 422


# --- the bearer answers before the body is validated (2026-10-28 review M-3) ---

_SCHEMA_FIELDS = ("agent_matrix_user_id", "agent_profile_id", "rtc_session_id")


@pytest.mark.asyncio
async def test_prewarm_missing_bearer_401_before_body_validation(app):
    """No credential -> 401 even for a body that would fail validation; the
    response never spells out PrewarmRequest's fields."""
    async with _client(app) as c:
        for body in ({}, {"agent_profile_id": "x" * 500}, {"rtc_session_id": 5}):
            r = await c.post("/api/narramessenger/prewarm", json=body)
            assert r.status_code == 401, (body, r.text)
            assert not any(f in r.text for f in _SCHEMA_FIELDS), r.text
        rs = await c.get("/api/narramessenger/prewarm/status",
                         params={"agent_profile_id": "x" * 500})
        assert rs.status_code == 401, rs.text
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_wrong_bearer_403_before_body_validation(app):
    """A resolvable identifier with a wrong bearer -> 403, not the 422 the
    over-long rtc_session_id would earn an authenticated caller."""
    body = {"agent_matrix_user_id": "@agent-x:hs", "rtc_session_id": "r" * 500}
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=body,
                         headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403, r.text
        assert not any(f in r.text for f in _SCHEMA_FIELDS), r.text
        ok = await c.post("/api/narramessenger/prewarm", json=body, headers=AUTH)
        assert ok.status_code == 422  # authenticated: body validation still applies
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_idempotent_second_call_already_warm(app, monkeypatch):
    async def alive(url, **kw):
        return True
    monkeypatch.setattr(nm, "executor_healthy", alive)
    async with _client(app) as c:
        await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        await asyncio.sleep(0)
        r2 = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r2.status_code == 202
    assert r2.json()["status"] == "already_warm"


@pytest.mark.asyncio
async def test_prewarm_inflight_dedup_single_ensure(app, monkeypatch):
    """Two rapid POSTs while the first ensure is still in flight must both
    answer 202 warming but spawn only ONE warmer task (stampede guard)."""
    release = asyncio.Event()

    async def hanging_ensure(user_id, **kw):
        app.state.ensured.append(user_id)
        await release.wait()
        return nm.ExecutorEnsureResult(url="http://nx-exec-user-1:8020", cold_started=True)
    monkeypatch.setattr(nm, "ensure_executor", hanging_ensure)

    async with _client(app) as c:
        r1 = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        await asyncio.sleep(0)  # let the warmer reach the hanging ensure
        r2 = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        assert r1.status_code == r2.status_code == 202
        assert r1.json()["status"] == r2.json()["status"] == "warming"
        assert app.state.ensured == ["user_1"]  # ONE ensure, not two
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert nm._PREWARM_STATE["user_1"]["status"] == "ready"


@pytest.mark.asyncio
async def test_prewarm_no_broker_skipped(app, monkeypatch):
    monkeypatch.setattr(nm, "broker_url", lambda: None)
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r.status_code == 202
    assert r.json()["status"] == "skipped"
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_ensure_failure_drops_entry_and_retries(app, monkeypatch):
    async def broken_ensure(user_id, **kw):
        raise RuntimeError("broker down")
    monkeypatch.setattr(nm, "ensure_executor", broken_ensure)
    async with _client(app) as c:
        r1 = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        assert r1.status_code == 202
        assert r1.json()["status"] == "warming"
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # A failed warm leaves NO ledger entry — a "failed" status would
        # carry no information the next POST could use.
        assert "user_1" not in nm._PREWARM_STATE
        rs = await c.get("/api/narramessenger/prewarm/status",
                         params={"agent_matrix_user_id": "@agent-x:hs"}, headers=AUTH)
        assert rs.status_code == 200
        assert rs.json() == {"ready": False}

        async def working_ensure(user_id, **kw):
            app.state.ensured.append(user_id)
            return nm.ExecutorEnsureResult(
                url="http://nx-exec-user-1:8020", cold_started=True
            )
        monkeypatch.setattr(nm, "ensure_executor", working_ensure)
        r2 = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        await asyncio.sleep(0)
    assert r2.status_code == 202
    assert r2.json()["status"] == "warming"  # a failed state must not wedge retries
    assert app.state.ensured == ["user_1"]


@pytest.mark.asyncio
async def test_prewarm_owner_lookup_failure_503(app, monkeypatch):
    async def owner_none(self, agent_id):
        return None
    monkeypatch.setattr(nm.AgentRepository, "resolve_owner", owner_none)
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_status_reports_ready_from_state(app, monkeypatch):
    async def alive(url, **kw):
        return True
    monkeypatch.setattr(nm, "executor_healthy", alive)
    async with _client(app) as c:
        await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        await asyncio.sleep(0)
        r = await c.get("/api/narramessenger/prewarm/status",
                        params={"agent_matrix_user_id": "@agent-x:hs"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"ready": True}


@pytest.mark.asyncio
async def test_status_unknown_state_not_ready(app):
    async with _client(app) as c:
        r = await c.get("/api/narramessenger/prewarm/status",
                        params={"agent_matrix_user_id": "@agent-x:hs"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"ready": False}


def test_prewarm_paths_are_auth_exempt():
    from backend.auth import AUTH_EXEMPT_PATHS
    assert "/api/narramessenger/prewarm" in AUTH_EXEMPT_PATHS
    assert "/api/narramessenger/prewarm/status" in AUTH_EXEMPT_PATHS


@pytest.mark.asyncio
async def test_prewarm_authorises_rolling_a_stale_image(monkeypatch):
    """Prewarm is the right place to roll: not a run, nobody on the
    container, and its whole purpose is moving cold-start out of the user's
    turn. Left at the default, the first interaction after every image
    rebuild warms the OLD image and then pays the full replacement inline —
    a regression only visible as an occasional slow first turn."""
    captured = {}

    async def fake_ensure(user_id, *, allow_stale_replace=False, timeout=120.0):
        captured["allow"] = allow_stale_replace
        from types import SimpleNamespace

        return SimpleNamespace(url="http://x:8020", cold_started=True)

    async def verdict(user_id, *, active_run_id=None):
        captured["active_run_id"] = active_run_id
        return True

    async def ready(url):
        return None

    monkeypatch.setattr(nm, "ensure_executor", fake_ensure)
    monkeypatch.setattr(nm, "no_live_recorded_run_for", verdict)
    monkeypatch.setattr(nm, "wait_until_ready", ready)

    await nm._do_prewarm("u1", gen=next(nm._PREWARM_GEN))

    assert captured["allow"] is True
    # Nothing to exclude — prewarm is not a run.
    assert captured["active_run_id"] is None


@pytest.mark.asyncio
async def test_prewarm_defers_the_roll_while_a_run_is_live(monkeypatch):
    """The negative half of the pair above. Without it, hard-coding
    `allow_stale_replace=True` in _do_prewarm passes every test in this file
    — and that constant is precisely "prewarm authorises destroying a
    container unconditionally", the 2026-07-31 shape."""
    captured = {}

    async def fake_ensure(user_id, *, allow_stale_replace=False, timeout=120.0):
        from types import SimpleNamespace

        captured["allow"] = allow_stale_replace
        return SimpleNamespace(url="http://x:8020", cold_started=True)

    async def busy(user_id, *, active_run_id=None):
        return False

    async def ready(url):
        return None

    monkeypatch.setattr(nm, "ensure_executor", fake_ensure)
    monkeypatch.setattr(nm, "no_live_recorded_run_for", busy)
    monkeypatch.setattr(nm, "wait_until_ready", ready)

    await nm._do_prewarm("u1", gen=next(nm._PREWARM_GEN))
    assert captured["allow"] is False
