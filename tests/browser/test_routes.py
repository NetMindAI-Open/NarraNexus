"""
@file_name: test_routes.py
@author:
@date: 2026-09-22
@description: Tests for the browser HTTP + WebSocket routes.

The WS tests are the interesting half. Two properties are pinned because both
were wrong in the first draft and neither shows up in a happy-path click-through:

* **Frames push without waiting for client traffic.** A single read-then-write
  loop only sends when the client says something, so an idle panel freezes
  while the browser is producing frames.
* **A full queue drops the OLDEST frame, never the newest.** A screencast
  frame is a whole picture of the page, so keeping the newest means the user
  still sees every state the page settles in (铁律 #16) — keeping the oldest
  would show them a stale page and call it backpressure.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import browser as browser_routes
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy
from narranexus.platform.browser._browser_impl.session import BrowserSession


class FakeCdp:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.frame_handler = None
        self.is_open = True

    async def call(self, method, params=None, **_kw):
        self.calls.append((method, params or {}))
        return {}

    async def start_screencast(self, *, on_frame, **_kw):
        self.frame_handler = on_frame

    async def stop_screencast(self):
        self.frame_handler = None

    async def capture_frame(self):
        pass

    async def close(self):
        self.is_open = False


@pytest.fixture
def app_and_session(monkeypatch):
    app = FastAPI()
    app.include_router(browser_routes.router, prefix="/api/browser")
    app.include_router(browser_routes.ws_router)
    app.middleware("http")(browser_routes.auth_middleware)
    # The real auth middleware accepts a bare X-User-Id only in local mode, and
    # the mode is resolved from the environment — pin it, or any earlier test
    # (or a developer's .env) that leaves DB_HOST / DATABASE_URL behind turns
    # every request here into a 401. Cloud-mode cases patch _is_cloud_mode.
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("NARRANEXUS_BROWSER_STREAM_SECRET", "test-secret-" * 4)

    async def owned(request, agent_id):
        from fastapi import HTTPException
        if request.state.user_id != "u1" or agent_id != "a1":
            raise HTTPException(status_code=403, detail="Not your agent")

    monkeypatch.setattr(browser_routes, "assert_owned", owned)

    cdp = FakeCdp()
    session = BrowserSession(
        cdp=cdp,
        policy=BrowserPolicy(),
        audit=lambda _row: None,
        turn_id="t1",
        thread_id="th1",
    )
    browser_routes.register_session("s1", session)
    yield app, session, cdp
    browser_routes.drop_session("s1")


# ── runtime endpoints ────────────────────────────────────────────────────────


def test_runtime_status_reports_absent_without_an_install(app_and_session, monkeypatch, tmp_path):
    import shlex
    import sys
    app, _s, _c = app_and_session
    monkeypatch.setattr(browser_routes, "_service", None)
    # An empty dir, not a hoped-for absence: this suite must not depend on
    # whether the machine running it happens to have the browser installed.
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", str(tmp_path / "empty-browser-home"))

    with TestClient(app, headers={"X-User-Id": "u1"}) as client:
        body = client.get("/api/browser/runtime").json()

    assert body["state"] == "absent"
    assert body["progress"] is None
    recovery = body["manual_install"]
    assert shlex.split(recovery["command"]) == recovery["argv"]
    assert recovery["argv"][:4] == [sys.executable, "-m", "narranexus.platform.browser", "install"]
    assert recovery["root"] == str(tmp_path / "empty-browser-home")
    assert not (tmp_path / "empty-browser-home").exists()


def test_install_failure_is_a_200_with_a_reason_not_a_500(app_and_session, monkeypatch, tmp_path):
    """The install card has to render the reason; a 500 gives it nothing."""
    app, _s, _c = app_and_session
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", str(tmp_path / "runtime"))

    # A failing downloader is INJECTED rather than letting the default one run:
    # the default reaches the vendor CDN, and a test suite that quietly
    # downloads over the network is slow, flaky offline, and lies about what
    # it is covering.
    from narranexus.platform.browser._browser_impl.install import InstallOutcome
    from narranexus.platform.browser.browser_service import BrowserService

    async def dead_cdn(*, on_progress, cancelled):
        raise RuntimeError("mirror unreachable")

    monkeypatch.setattr(
        browser_routes,
        "_service",
        BrowserService(locate=lambda: None, probe=lambda _p: None, downloader=dead_cdn),
    )
    assert InstallOutcome  # imported for the contract it documents

    with TestClient(app, headers={"X-User-Id": "u1"}) as client:
        res = client.post("/api/browser/runtime/install")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "mirror unreachable" in body["error"]
    assert body["manual_install"] == body["status"]["manual_install"]
    assert body["manual_install"]["root"] == str(tmp_path / "runtime")
    assert "--manifest-url" in body["manual_install"]["command"]
    assert "--download-host" in body["manual_install"]["command"]


# ── the relay ────────────────────────────────────────────────────────────────
#
# The stream's BEHAVIOUR (frames push without client traffic, oldest frame
# dropped under pressure, control handed back on disconnect) is tested where
# it is implemented — tests/browser/test_stream_bridge.py, in the process that
# owns the session. Duplicating those assertions against the relay would test
# a pass-through and drift from the real rules.
#
# What belongs here is what the RELAY owes the panel.


def test_an_unreachable_module_host_tells_the_panel_why(app_and_session, monkeypatch):
    """The MCP host may be restarting, or the agent may have no browser open.
    A silent empty canvas gives the user nothing to act on."""
    monkeypatch.setenv("MCP_PORT", "1")  # nothing listens
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.delenv("MCP_HOST", raising=False)

    app, _s, _c = app_and_session
    with TestClient(app) as client:
        with client.websocket_connect("/ws/browser/a1?x_user_id=u1") as ws:
            ws.send_json({"type": "auth", "user_id": "u1"})
            msg = ws.receive_json()

    assert msg["type"] == "error"
    assert "unavailable" in msg["error"]


def test_the_panel_connects_to_the_backend_not_the_module_port(app_and_session):
    """The MCP port is not a public surface: routing the panel through the
    backend is what keeps the stream behind the identity checks."""
    app, _s, _c = app_and_session
    paths = [r.path for r in browser_routes.ws_router.routes]
    assert paths == ["/ws/browser/{agent_id}"]
    assert app is not None


def test_internal_relay_bypasses_environment_proxy_and_signs_agent(app_and_session, monkeypatch):
    from unittest.mock import AsyncMock

    import websockets

    from narranexus.platform.browser._browser_impl.stream_auth import HEADER, verify_stream_token

    app, _, _ = app_and_session
    monkeypatch.setenv("HTTP_PROXY", "http://unreachable-proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://unreachable-proxy.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "http://unreachable-proxy.invalid:8080")
    connect = AsyncMock(side_effect=ConnectionError("test host unavailable"))
    monkeypatch.setattr(websockets, "connect", connect)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/browser/a1?x_user_id=u1") as ws:
            ws.send_json({"type": "auth", "user_id": "u1"})
            assert ws.receive_json()["code"] == "stream_unavailable"
    connect.assert_awaited_once()
    options = connect.call_args.kwargs
    assert "proxy" in options and options["proxy"] is None
    assert verify_stream_token("a1", options["additional_headers"][HEADER])
    assert not verify_stream_token("other", options["additional_headers"][HEADER])


def test_unauthenticated_http_is_rejected(app_and_session):
    app, _, _ = app_and_session
    with TestClient(app) as client:
        assert client.get("/api/browser/runtime").status_code == 401
        assert client.get("/api/browser/notices/a1").status_code == 401


def test_login_notices_require_agent_ownership(app_and_session):
    app, _, _ = app_and_session
    with TestClient(app, headers={"X-User-Id": "u1"}) as client:
        assert client.get("/api/browser/notices/someone-else").status_code == 403


def test_the_retired_approval_endpoints_are_gone(app_and_session):
    """Nothing ever raised an approval, so neither the list nor the
    write endpoint survives — a write endpoint no id can reach is attack
    surface with no function."""
    app, _, _ = app_and_session
    with TestClient(app, headers={"X-User-Id": "u1"}) as client:
        assert client.get("/api/browser/approvals/a1").status_code == 404
        assert client.post("/api/browser/approvals/appr_x",
                           json={"decision": "allow", "lifetime": "always"}).status_code == 404


@pytest.mark.parametrize("path", ["/api/browser/runtime/install", "/api/browser/runtime/install/cancel"])
def test_runtime_installation_is_local_only(app_and_session, monkeypatch, path):
    """In cloud the runtime would land in another container than the sessions,
    and any signed-in user could trigger a machine-wide download — the same
    local-only gate the source and mode endpoints already had."""
    from unittest.mock import AsyncMock
    app, _, _ = app_and_session
    service = browser_routes.get_service()
    install = AsyncMock()
    monkeypatch.setattr(service, "install", install)
    monkeypatch.setattr(service, "cancel_install", lambda: install())
    monkeypatch.setattr(browser_routes, "_is_cloud_mode", lambda: True)
    with TestClient(app, headers={"X-User-Id": "u1"}) as client:
        assert client.post(path).status_code == 403
    install.assert_not_called()


@pytest.mark.parametrize("agent,user,anchor", [("a1", "u1", "wrong"), ("a2", "u1", "u1"), ("a1", "other", "other")])
def test_socket_auth_and_ownership_precede_upstream(app_and_session, monkeypatch, agent, user, anchor):
    from unittest.mock import AsyncMock
    import websockets

    upstream = AsyncMock()
    monkeypatch.setattr(websockets, "connect", upstream)
    app, _, _ = app_and_session
    with TestClient(app) as client, client.websocket_connect(f"/ws/browser/{agent}?x_user_id={anchor}") as ws:
        ws.send_json({"type": "auth", "user_id": user})
        assert ws.receive_json()["code"] == "auth_failed"
    upstream.assert_not_awaited()


def test_browser_origin_is_checked_before_auth(app_and_session):
    from starlette.websockets import WebSocketDisconnect
    app, _, _ = app_and_session
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/browser/a1?x_user_id=u1", headers={"Origin": "https://evil.example"}):
            pass


def test_cloud_socket_rejects_invalid_token(app_and_session, monkeypatch):
    import backend.auth
    from backend.plugin_sdk_host import install_web_host
    install_web_host()
    monkeypatch.setattr(backend.auth, "_is_cloud_mode", lambda: True)
    monkeypatch.setattr(browser_routes, "_is_cloud_mode", lambda: True)
    app, _, _ = app_and_session
    with TestClient(app) as client, client.websocket_connect("/ws/browser/a1?x_user_id=u1") as ws:
        ws.send_json({"type": "auth", "user_id": "u1", "token": "invalid"})
        assert ws.receive_json()["code"] == "auth_failed"


def test_relay_uses_configured_mcp_host_and_base_path(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "https://module-host.internal/internal")
    assert browser_routes._stream_url("agent/a") == "wss://module-host.internal/internal/browser/agent%2Fa/stream"
    monkeypatch.delenv("MCP_BASE_URL")
    monkeypatch.setenv("MCP_HOST", "mcp")
    monkeypatch.setenv("MCP_PORT", "7802")
    assert browser_routes._stream_url("agent") == "ws://mcp:7802/browser/agent/stream"
