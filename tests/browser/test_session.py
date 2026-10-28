"""
@file_name: test_session.py
@author:
@date: 2026-09-22
@description: Tests for BrowserSession — policy + control + CDP, assembled.

This is where the pieces meet, so the tests are about their *interaction*
rather than about each piece again:

* ordinary web navigation needs no permission; non-web URLs never reach CDP;
* user input is gated by the arbiter, and an agent action waits instead of
  failing during a takeover;
* frames fan out to subscribers and a broken subscriber does not take the
  session down;
* every decision that matters produces an audit row, because a missing DB row
  is stronger evidence than a missing log line (incident lesson #5).
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
from narranexus.platform.browser._browser_impl.session import BrowserSession


class FakeCdp:
    """Records calls; never touches a browser."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.frame_handler = None
        self.closed = False
        self.frames_seen = 0
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
        self.closed = True
        self.is_open = False

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


def allow_all() -> BrowserPolicy:
    return BrowserPolicy()


def make_session(policy: BrowserPolicy | None = None) -> tuple[BrowserSession, FakeCdp, list]:
    cdp = FakeCdp()
    audit: list[dict] = []
    session = BrowserSession(
        cdp=cdp,
        policy=policy or allow_all(),
        audit=audit.append,
        turn_id="t1",
        thread_id="th1",
    )
    return session, cdp, audit


# ── navigation is policy-gated ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allowed_navigation_reaches_cdp():
    s, cdp, _ = make_session()
    result = await s.navigate("https://github.com/")
    assert result["ok"] is True
    assert "Page.navigate" in cdp.methods()


@pytest.mark.asyncio
async def test_legacy_denied_navigation_reaches_cdp():
    policy = BrowserPolicy.from_dict({"default_origin_policy": {"access": "deny"}})
    s, cdp, _ = make_session(policy)

    result = await s.navigate("https://evil.example/")

    assert result["ok"] is True
    assert result["outcome"] == "OK"
    assert "Page.navigate" in cdp.methods()


@pytest.mark.asyncio
async def test_legacy_ask_cannot_request_site_permission():
    s, _cdp, _ = make_session(BrowserPolicy.from_dict({"default_origin_policy": {"access": "ask"}}))
    result = await s.navigate("https://unknown.example/")
    assert result["outcome"] == "OK"
    assert "human_action" not in result


@pytest.mark.asyncio
async def test_non_http_url_is_rejected():
    s, cdp, _ = make_session()
    result = await s.navigate("file:///etc/passwd")
    assert result["ok"] is False
    assert "Page.navigate" not in cdp.methods()


@pytest.mark.asyncio
async def test_navigation_writes_an_audit_row_whether_allowed_or_not():
    """Incident lesson #5: the DB trace is the evidence, and 'the expected row
    is missing' has to be a meaningful statement."""
    s, _cdp, audit = make_session()
    await s.navigate("https://github.com/")
    await s.navigate("file:///etc/passwd")

    kinds = [row["event"] for row in audit]
    assert kinds.count("navigate") == 2
    assert {row["verdict"] for row in audit} == {"allow", "deny"}


# ── control ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_input_is_dropped_until_takeover():
    s, cdp, _ = make_session()
    await s.handle_user_input({"kind": "mouse", "type": "mousePressed", "x": 1, "y": 2})
    assert "Input.dispatchMouseEvent" not in cdp.methods()


@pytest.mark.asyncio
async def test_user_input_reaches_cdp_after_takeover():
    s, cdp, _ = make_session()
    s.control.user_take_control()
    await s.handle_user_input({"kind": "mouse", "type": "mousePressed", "x": 1, "y": 2})
    assert "Input.dispatchMouseEvent" in cdp.methods()


@pytest.mark.asyncio
async def test_unrecognised_user_input_is_not_forwarded():
    s, cdp, _ = make_session()
    s.control.user_take_control()
    await s.handle_user_input({"kind": "exec", "cmd": "rm -rf /"})
    assert cdp.methods() == []


@pytest.mark.asyncio
async def test_resize_clamps_dimensions_and_uses_fixed_device_metrics():
    session, cdp, _ = make_session()
    assert await session.resize_viewport(100, 5000, "watcher")
    assert cdp.calls == [("Emulation.setDeviceMetricsOverride", {
        "width": 240, "height": 1440, "deviceScaleFactor": 1, "mobile": False,
    })]
    assert await session.resize_viewport(9000, -1, "watcher")
    assert cdp.calls[-1][1]["width"] == 1920
    assert cdp.calls[-1][1]["height"] == 240
    assert await session.resize_viewport(9000, -1, "watcher")
    assert len(cdp.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height", [(True, 500), (500, False), ("500", 500), (500.5, 500), (None, 500)])
async def test_resize_ignores_malformed_dimensions(width, height):
    session, cdp, _ = make_session()
    assert not await session.resize_viewport(width, height, "watcher")
    assert not cdp.calls


@pytest.mark.asyncio
async def test_resize_does_not_wait_for_another_connections_takeover():
    session, cdp, _ = make_session()
    await session.take_control("owner")
    async with session._operation_lock:
        assert not await asyncio.wait_for(session.resize_viewport(400, 600, "watcher"), 0.2)
    assert not cdp.calls
    assert await session.resize_viewport(400, 600, "owner")
    assert cdp.calls[-1][1]["width"] == 400


@pytest.mark.asyncio
async def test_resize_waits_for_current_action_and_rechecks_ownership():
    session, cdp, _ = make_session()
    async with session._operation_lock:
        resize = asyncio.create_task(session.resize_viewport(400, 600, "watcher"))
        await asyncio.sleep(0)
        assert not resize.done()
        assert not cdp.calls
        session.control.user_take_control("owner")
    assert not await resize
    assert not cdp.calls


@pytest.mark.asyncio
async def test_resize_of_closed_session_is_ignored():
    session, cdp, _ = make_session()
    await session.close()
    assert not await session.resize_viewport(400, 600, "watcher")
    assert not cdp.calls


@pytest.mark.asyncio
async def test_agent_navigation_waits_during_takeover_then_proceeds():
    s, cdp, _ = make_session()
    s.control.user_take_control()

    nav = asyncio.create_task(s.navigate("https://github.com/"))
    await asyncio.sleep(0.02)
    assert not nav.done(), "must queue, not fail"
    assert "Page.navigate" not in cdp.methods()

    s.control.user_release_control()
    result = await asyncio.wait_for(nav, timeout=2)
    assert result["ok"] is True


# ── frames ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_frames_fan_out_to_subscribers():
    s, cdp, _ = make_session()
    got_a, got_b = [], []
    await s.start_stream()
    s.subscribe(got_a.append)
    s.subscribe(got_b.append)

    cdp.frame_handler("base64data", {"timestamp": 1})

    assert got_a and got_b
    assert got_a[0]["data"] == "base64data"


@pytest.mark.asyncio
async def test_a_broken_subscriber_does_not_stop_the_others():
    s, cdp, _ = make_session()
    good: list = []
    await s.start_stream()
    s.subscribe(lambda _f: (_ for _ in ()).throw(ValueError("ui bug")))
    s.subscribe(good.append)

    cdp.frame_handler("x", {})

    assert len(good) == 1
    assert s.is_open is True


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    s, cdp, _ = make_session()
    got: list = []
    await s.start_stream()
    unsub = s.subscribe(got.append)
    unsub()

    cdp.frame_handler("x", {})

    assert got == []


# ── lifecycle ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_stops_the_stream_and_closes_cdp():
    s, cdp, _ = make_session()
    await s.start_stream()
    await s.close()
    assert cdp.closed is True
    assert s.is_open is False


@pytest.mark.asyncio
async def test_close_releases_a_queued_agent_action():
    s, _cdp, _ = make_session()
    s.control.user_take_control()
    nav = asyncio.create_task(s.navigate("https://github.com/"))
    await asyncio.sleep(0.02)

    await s.close()

    result = await asyncio.wait_for(nav, timeout=2)
    assert result["ok"] is False
    assert result["outcome"] == "ERROR"


@pytest.mark.asyncio
async def test_navigate_after_close_is_refused_not_crashed():
    s, _cdp, _ = make_session()
    await s.close()
    result = await s.navigate("https://github.com/")
    assert result["ok"] is False
