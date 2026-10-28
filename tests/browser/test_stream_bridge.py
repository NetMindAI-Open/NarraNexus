"""
@file_name: test_stream_bridge.py
@author:
@date: 2026-09-23
@description: Authenticated stream lifecycle and competing-panel integration tests.
"""
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from narranexus.platform.browser._browser_impl.stream_auth import HEADER, stream_token
from narranexus.platform.browser.stream_bridge import stream_routes
from tests.browser.test_session import make_session


@pytest.fixture
def host_and_session(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BROWSER_STREAM_SECRET", "test-secret-" * 4)
    session, cdp, _ = make_session()
    sessions = {"a1": session}
    app = Starlette(routes=stream_routes(sessions.get))
    return app, session, cdp, sessions


def connect(client, agent_id="a1", **kwargs):
    return client.websocket_connect(f"/browser/{agent_id}/stream",
                                    headers={HEADER: stream_token(agent_id), **kwargs})


def test_no_session_stays_connected_until_session_starts(host_and_session):
    app, session, _, sessions = host_and_session
    sessions.clear()
    with TestClient(app) as client, connect(client) as ws:
        assert ws.receive_json() == {"type": "idle", "reason": "awaiting_session"}
        client.portal.call(lambda: sessions.update(a1=session))
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_frames_replay_to_another_viewer_but_not_after_stream_stops(host_and_session):
    app, session, cdp, _ = host_and_session
    with TestClient(app) as client:
        with connect(client) as ws:
            assert ws.receive_json()["type"] == "hello"
            client.portal.call(lambda: cdp.frame_handler("last-frame", {"timestamp": 1}))
            assert ws.receive_json()["data"] == "last-frame"
            with connect(client) as watcher:
                assert watcher.receive_json()["type"] == "hello"
                assert watcher.receive_json()["data"] == "last-frame"
        with connect(client) as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
            client.portal.call(lambda: cdp.frame_handler("fresh-frame", {"timestamp": 2}))
            assert ws.receive_json()["data"] == "fresh-frame"
    assert session.frames_delivered == 2


def test_control_is_broadcast_and_only_owner_can_drive_or_release(host_and_session):
    app, session, cdp, _ = host_and_session
    with TestClient(app) as client, connect(client) as owner, connect(client) as watcher:
        owner_id = owner.receive_json()["connection_id"]
        watcher.receive_json()
        owner.send_json({"type": "take_control"})
        assert owner.receive_json()["control"]["can_control"]
        state = watcher.receive_json()["control"]
        assert not state["can_control"]
        assert state["owner_connection_id"] == owner_id
        watcher.send_json({"type": "take_control"})
        assert watcher.receive_json()["code"] == "control_busy"
        watcher.send_json({"type": "release_control"})
        watcher.send_json({"type": "input", "event": {"kind": "text", "text": "ignored"}})
        watcher.send_json({"type": "ping"})
        assert watcher.receive_json()["type"] == "pong"
        assert session.control.holder == "user"
        assert not cdp.calls
        owner.send_json({"type": "input", "event": {"kind": "text", "text": "accepted"}})
        owner.send_json({"type": "ping"})
        assert owner.receive_json()["type"] == "pong"
        assert cdp.calls == [("Input.insertText", {"text": "accepted"})]
    assert session.control.holder == "agent"


def test_watcher_disconnect_cannot_release_owner(host_and_session):
    app, session, _, _ = host_and_session
    with TestClient(app) as client, connect(client) as owner:
        owner.receive_json()
        owner.send_json({"type": "take_control"})
        owner.receive_json()
        with connect(client) as watcher:
            assert not watcher.receive_json()["control"]["can_control"]
        assert session.control.holder == "user"


def test_owner_disconnect_broadcasts_release(host_and_session):
    app, _, _, _ = host_and_session
    with TestClient(app) as client, connect(client) as watcher:
        watcher.receive_json()
        with connect(client) as owner:
            owner.receive_json()
            owner.send_json({"type": "take_control"})
            owner.receive_json()
            watcher.receive_json()
        assert watcher.receive_json()["control"]["holder"] == "agent"


def test_closed_session_returns_to_idle_then_attaches_replacement(host_and_session):
    app, session, _, sessions = host_and_session
    with TestClient(app) as client, connect(client) as ws:
        ws.receive_json()
        client.portal.call(session.close)
        while ws.receive_json()["type"] != "idle":
            pass
        replacement, cdp, _ = make_session()
        client.portal.call(lambda: sessions.update(a1=replacement))
        assert ws.receive_json()["type"] == "hello"
        client.portal.call(lambda: cdp.frame_handler("new", {}))
        assert ws.receive_json()["data"] == "new"


def test_bad_messages_do_not_break_live_stream(host_and_session):
    app, _, _, _ = host_and_session
    with TestClient(app) as client, connect(client) as ws:
        ws.receive_json()
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "invalid_message"
        ws.send_json([])
        assert ws.receive_json()["code"] == "invalid_message"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


@pytest.mark.parametrize("headers", [{}, {HEADER: "forged"}, {"origin": "https://evil.example"}])
def test_internal_stream_rejects_untrusted_clients(host_and_session, headers):
    app, _, _, _ = host_and_session
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/browser/a1/stream", headers=headers):
                pass


def test_internal_token_is_bound_to_agent(host_and_session):
    app, _, _, _ = host_and_session
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/browser/a2/stream", headers={HEADER: stream_token("a1")}):
                pass


def test_resize_before_session_is_ignored_and_hello_allows_retry(host_and_session):
    app, session, cdp, sessions = host_and_session
    sessions.clear()
    with TestClient(app) as client, connect(client) as ws:
        assert ws.receive_json()["type"] == "idle"
        ws.send_json({"type": "resize", "width": 392, "height": 617})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        assert not cdp.calls
        client.portal.call(lambda: sessions.update(a1=session))
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "resize", "width": 392, "height": 617})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        assert cdp.calls[-1] == ("Emulation.setDeviceMetricsOverride", {
            "width": 392, "height": 617, "deviceScaleFactor": 1, "mobile": False,
        })


def test_resize_uses_connection_identity_during_takeover(host_and_session):
    app, _, cdp, _ = host_and_session
    with TestClient(app) as client, connect(client) as owner, connect(client) as watcher:
        owner.receive_json()
        watcher.receive_json()
        owner.send_json({"type": "take_control"})
        owner.receive_json()
        watcher.receive_json()
        watcher.send_json({"type": "resize", "width": 500, "height": 600})
        watcher.send_json({"type": "ping"})
        assert watcher.receive_json()["type"] == "pong"
        assert not cdp.calls
        owner.send_json({"type": "resize", "width": 400, "height": 700})
        owner.send_json({"type": "ping"})
        assert owner.receive_json()["type"] == "pong"
        assert cdp.calls[-1][1]["width"] == 400


def test_login_callbacks_require_actual_control_and_distinguish_disconnect(host_and_session):
    _, session, _, sessions = host_and_session
    changes = []

    async def changed(agent_id, current, connection_id, event):
        changes.append((agent_id, current, connection_id, event))

    app = Starlette(routes=stream_routes(sessions.get, on_login_control=changed))
    with TestClient(app) as client, connect(client) as watcher:
        watcher.receive_json()
        with connect(client) as owner:
            owner_id = owner.receive_json()["connection_id"]
            owner.send_json({"type": "take_control"})
            owner.receive_json()
            watcher.receive_json()
            owner.send_json({"type": "ping"})
            assert owner.receive_json()["type"] == "pong"
            watcher.send_json({"type": "release_control"})
            watcher.send_json({"type": "ping"})
            assert watcher.receive_json()["type"] == "pong"
            assert [change[3] for change in changes] == ["take"]
        watcher.receive_json()
        client.portal.call(lambda: None)
        assert changes[-1] == ("a1", session, owner_id, "disconnect")
        watcher.send_json({"type": "take_control"})
        watcher.receive_json()
        watcher.send_json({"type": "release_control"})
        watcher.receive_json()
        watcher.send_json({"type": "ping"})
        assert watcher.receive_json()["type"] == "pong"
        assert [change[3] for change in changes] == ["take", "disconnect", "take", "release"]


def test_login_receipt_failure_is_visible_without_breaking_control(host_and_session):
    _, session, _, sessions = host_and_session

    async def failed(*_):
        raise RuntimeError("Receipt storage unavailable")

    app = Starlette(routes=stream_routes(sessions.get, on_login_control=failed))
    with TestClient(app) as client, connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "take_control"})
        assert ws.receive_json()["control"]["can_control"]
        assert ws.receive_json()["code"] == "login_update_failed"
        ws.send_json({"type": "release_control"})
        assert ws.receive_json()["control"]["holder"] == "agent"
        assert ws.receive_json()["code"] == "login_update_failed"
        assert session.control.holder == "agent"
