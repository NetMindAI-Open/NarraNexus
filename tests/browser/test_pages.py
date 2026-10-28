"""@file_name: test_pages.py
@description: Multi-page discovery, ownership and independent viewing regressions.
"""
import asyncio

import pytest

from narranexus.platform.browser._browser_impl.cdp import CdpSession
from tests.browser.test_cdp import FakeSocket
from tests.browser.test_session import FakeCdp, make_session


@pytest.mark.asyncio
async def test_flattened_targets_route_frames_and_detach_independently():
    import json

    socket = FakeSocket()
    browser = CdpSession(socket=socket)
    first = browser.channel("s1")
    second = browser.channel("s2")
    frames = []
    try:
        await first.start_screencast(on_frame=lambda *args: frames.append(args))
        assert socket.sent[-1]["sessionId"] == "s1"
        await socket._inbox.put(json.dumps({
            "sessionId": "s2", "method": "Page.screencastFrame",
            "params": {"sessionId": 1, "data": "other"},
        }))
        await socket._inbox.put(json.dumps({
            "sessionId": "s1", "method": "Page.screencastFrame",
            "params": {"sessionId": 2, "data": "first"},
        }))
        await asyncio.sleep(0.01)
        assert frames == [("first", {})]
        acks = [row for row in socket.sent if row["method"] == "Page.screencastFrameAck"]
        assert {(row["sessionId"], row["params"]["sessionId"]) for row in acks} == {("s1", 2), ("s2", 1)}
        socket.auto_reply = False
        waiting = asyncio.create_task(first.call("Runtime.evaluate"))
        await asyncio.sleep(0)
        await socket.push_event("Target.detachedFromTarget", {"sessionId": "s1"})
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(waiting, 1)
        assert not first.is_open
        assert browser.is_open and second.is_open
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_viewing_does_not_change_agent_page_and_frames_are_page_scoped():
    session, first, _ = make_session()
    second = FakeCdp()
    session.pages.add("second", second, title="Second", url="https://two.example")
    original = session.pages.active_id
    seen = []
    session.subscribe(seen.append, page_id="second")
    await session.start_stream(page_id="second")
    second.frame_handler("second-image", {})
    assert seen[-1]["page_id"] == "second"
    assert session.pages.active_id == original
    assert (await session.navigate("https://one.example"))["page_id"] == original
    assert first.calls[-1][0] == "Page.navigate"
    assert not second.calls
    await session.close()


@pytest.mark.asyncio
async def test_watched_static_page_refreshes_after_resize_only_when_size_changes():
    session, cdp, _ = make_session()
    seen = []
    unsubscribe = session.subscribe(seen.append)

    async def capture_frame():
        width, height = session.pages.current.viewport
        cdp.frame_handler("resized-image", {"deviceWidth": width, "deviceHeight": height})

    cdp.capture_frame = capture_frame
    try:
        await session.start_stream()
        assert await session.resize_viewport(400, 700, "viewer")
        assert len(seen) == 1
        assert seen[0]["meta"] == {"deviceWidth": 400, "deviceHeight": 700}
        assert await session.resize_viewport(400, 700, "viewer")
        assert len(seen) == 1
        unsubscribe()
        await session.stop_stream(session.pages.active_id)
        assert await session.resize_viewport(500, 800, "viewer")
        assert len(seen) == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_resize_waits_until_initial_screenshot_has_finished():
    session, cdp, _ = make_session()
    capturing, release = asyncio.Event(), asyncio.Event()

    async def start_screencast(**_):
        capturing.set()
        await release.wait()

    cdp.start_screencast = start_screencast
    streaming = asyncio.create_task(session.start_stream())
    await capturing.wait()
    resizing = asyncio.create_task(session.resize_viewport(400, 700, "viewer"))
    try:
        await asyncio.sleep(0)
        assert not cdp.calls, "Chrome must not restore screenshot metrics over a concurrent resize"
    finally:
        release.set()
        await asyncio.gather(streaming, resizing)
        await session.close()
    assert cdp.calls[-1][1]["height"] == 700


@pytest.mark.asyncio
async def test_owner_switch_resets_old_inputs_and_stale_input_cannot_hit_new_page():
    session, first, _ = make_session()
    second = FakeCdp()
    session.pages.add("second", second, title="Second", url="https://two.example")
    original = session.pages.active_id
    assert await session.take_control("owner", page_id=original)
    await session.handle_user_input({"kind": "key", "key": "Shift", "type": "keyDown"}, "owner", page_id=original)
    assert not await session.select_user_page("second", "spectator")
    assert await session.select_user_page("second", "owner")
    assert first.calls[-1][1]["type"] == "keyUp"
    await session.handle_user_input({"kind": "text", "text": "stale"}, "owner", page_id=original)
    assert not second.calls
    await session.handle_user_input({"kind": "text", "text": "correct"}, "owner", page_id="second")
    assert second.calls[-1] == ("Input.insertText", {"text": "correct"})
    queued = asyncio.create_task(session.select_page(original))
    await asyncio.sleep(0)
    assert not queued.done()
    await session.release_control("owner")
    assert (await queued)["ok"]
    assert session.pages.active_id == original
    await session.close()


def test_stream_tabs_are_independent_between_viewers():
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from narranexus.platform.browser.stream_bridge import stream_routes
    from tests.browser.test_stream_bridge import connect

    session, first, _ = make_session()
    second = FakeCdp()
    session.pages.add("second", second, title="Second", url="https://two.example")
    app = Starlette(routes=stream_routes(lambda _: session))

    def until(ws, kind):
        while (message := ws.receive_json())["type"] != kind:
            pass
        return message

    with TestClient(app) as client, connect(client) as one, connect(client) as two:
        hello = one.receive_json()
        assert len(hello["pages"]) == 2
        assert two.receive_json()["selected_page_id"] == "main"
        one.send_json({"type": "select_page", "page_id": "second"})
        assert until(one, "pages")["selected_page_id"] == "second"
        client.portal.call(lambda: second.frame_handler("two", {}))
        assert until(one, "frame")["page_id"] == "second"
        two.send_json({"type": "ping"})
        assert two.receive_json()["type"] == "pong"
        assert session.pages.active_id == "main"
        one.send_json({"type": "take_control", "page_id": "second"})
        assert until(one, "control")["control"]["can_control"]
        assert until(two, "pages")["selected_page_id"] == "second"
        one.send_json({"type": "input", "page_id": "main", "event": {"kind": "text", "text": "stale"}})
        one.send_json({"type": "ping"})
        until(one, "pong")
        assert not second.calls
        one.send_json({"type": "select_page", "page_id": "missing"})
        assert until(one, "error")["code"] == "page_unavailable"


@pytest.mark.asyncio
async def test_manual_tabs_require_owner_preserve_old_page_and_reject_stale_navigation(monkeypatch):
    session, first, audit = make_session()
    second = FakeCdp()

    async def create():
        page = session.pages.add("manual", second)
        session.pages.select(page.id)
        return page

    monkeypatch.setattr(session.pages, "create", create)
    try:
        assert await session.new_user_page("spectator") is None
        assert len(session.pages.items) == 1
        assert await session.take_control("owner")
        await session.handle_user_input({"kind": "key", "key": "Shift", "type": "keyDown"}, "owner")
        assert await session.new_user_page("owner") == "manual"
        assert first.calls[-1][1]["type"] == "keyUp"
        assert len(session.pages.items) == 2
        assert not await session.navigate_user_page("manual", "https://example.com", "spectator")
        for url in ("javascript:alert(1)", "file:///tmp/private", "data:text/html,x", "", None):
            with pytest.raises(ValueError, match="HTTP"):
                await session.navigate_user_page("manual", url, "owner")
        with pytest.raises(ValueError, match="selected"):
            await session.navigate_user_page("main", "https://example.com", "owner")
        assert not second.calls
        assert await session.navigate_user_page("manual", "https://example.com/?token=secret", "owner")
        assert second.calls[-1] == ("Page.navigate", {"url": "https://example.com/?token=secret"})
        assert "secret" not in str(audit)
        queued = asyncio.create_task(session.select_page("manual"))
        await asyncio.sleep(0)
        assert not queued.done()
        await session.release_control("owner")
        assert (await queued)["page_id"] == "manual"
    finally:
        await session.close()


def test_stream_manual_tab_actions_acknowledge_and_keep_spectators_read_only(monkeypatch):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from narranexus.platform.browser.stream_bridge import stream_routes
    from tests.browser.test_stream_bridge import connect

    session, _, _ = make_session()
    second = FakeCdp()

    async def create():
        page = session.pages.add("manual", second)
        session.pages.select(page.id)
        return page

    monkeypatch.setattr(session.pages, "create", create)

    def until(ws, kind):
        while (message := ws.receive_json())["type"] != kind:
            pass
        return message

    with TestClient(Starlette(routes=stream_routes(lambda _: session))) as client, connect(client) as owner, connect(client) as spectator:
        owner.receive_json()
        spectator.receive_json()
        spectator.send_json({"type": "new_page"})
        assert until(spectator, "error")["code"] == "page_action_failed"
        owner.send_json({"type": "take_control", "page_id": "main"})
        assert until(owner, "control")["control"]["can_control"]
        owner.send_json({"type": "new_page"})
        assert until(owner, "page_action") == {"type": "page_action", "action": "new_page", "page_id": "manual"}
        spectator.send_json({"type": "navigate", "page_id": "manual", "url": "https://example.com"})
        assert until(spectator, "error")["code"] == "page_action_failed"
        assert not second.calls
        owner.send_json({"type": "navigate", "page_id": "main", "url": "https://example.com"})
        assert until(owner, "error")["code"] == "page_action_failed"
        owner.send_json({"type": "navigate", "page_id": "manual", "url": "https://example.com"})
        assert until(owner, "page_action")["action"] == "navigate"
        assert second.calls[-1] == ("Page.navigate", {"url": "https://example.com"})
