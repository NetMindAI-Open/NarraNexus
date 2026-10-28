"""
@file_name: test_robustness.py
@author:
@date: 2026-09-22
@description: Regression tests for browser concurrency, input and permission boundaries.
"""
import asyncio

import pytest

from narranexus.platform.browser._browser_impl.cdp import SCREENCAST_MIME_TYPE, input_events_for
from narranexus.platform.browser._browser_impl.control import ControlArbiter
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, origin_of
from tests.browser.test_session import make_session


def test_control_is_exclusive_to_one_connection():
    control = ControlArbiter()
    assert control.user_take_control("first")
    assert not control.user_take_control("second")
    assert not control.accept_user_input("second")
    assert not control.user_release_control("second")
    assert control.accept_user_input("first")
    assert control.to_dict()["owner_connection_id"] == "first"


@pytest.mark.asyncio
async def test_waiter_rechecks_takeover_after_release():
    control = ControlArbiter()
    control.user_take_control("first")
    waiter = asyncio.create_task(control.wait_for_turn())
    await asyncio.sleep(0)
    control.user_release_control("first")
    control.user_take_control("second")
    await asyncio.sleep(0)
    assert not waiter.done()
    control.user_release_control("second")
    await waiter


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leave_waiting_state():
    control = ControlArbiter()
    control.user_take_control("first")
    waiter = asyncio.create_task(control.wait_for_turn())
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert not control.to_dict()["agent_waiting"]


@pytest.mark.parametrize("event", [[], "bad", {"kind": "mouse", "x": float("nan"), "y": 2, "type": "mouseMoved"}])
def test_malformed_input_is_ignored(event):
    assert input_events_for(event) == []


@pytest.mark.parametrize("event", [
    {"kind": "key", "key": "a", "type": "keyUp"},
    {"kind": "key", "key": "a", "type": "keyDown", "modifiers": 2},
    {"kind": "key", "key": "a", "type": "keyDown", "metaKey": True},
])
def test_key_release_and_shortcuts_do_not_insert_text(event):
    assert not input_events_for(event)[0][1].get("text")


def test_keyboard_preserves_navigation_codes_and_modifiers():
    params = input_events_for({"kind": "key", "key": "Backspace", "type": "keyDown",
                               "code": "Backspace", "modifiers": 8})[0][1]
    assert params["windowsVirtualKeyCode"] == 8
    assert params["code"] == "Backspace"
    assert params["modifiers"] == 8


def test_mouse_move_does_not_imply_left_drag():
    params = input_events_for({"kind": "mouse", "type": "mouseMoved", "x": 1, "y": 2})[0][1]
    assert params["button"] == "none"
    assert params["buttons"] == 0
    assert params["clickCount"] == 0


@pytest.mark.asyncio
async def test_subscribe_replays_last_frame_on_static_page():
    session, cdp, _ = make_session()
    await session.start_stream()
    cdp.frame_handler("last", {"deviceWidth": 1280})
    frames = []
    session.subscribe(frames.append)
    assert frames[0]["data"] == "last"
    # The panel decodes by this, not by a format both sides merely agree on.
    assert frames[0]["mime_type"] == SCREENCAST_MIME_TYPE


@pytest.mark.asyncio
async def test_dead_cdp_is_not_reported_as_open():
    session, cdp, _ = make_session()
    cdp.is_open = False
    assert not session.is_open


@pytest.mark.asyncio
async def test_read_after_redirect_needs_no_approval_or_policy_refresh():
    session, cdp, _ = make_session()

    async def call(method, params=None, **kwargs):
        return {"result": {"value": "https://redirect.example/" if params["expression"] == "location.href" else {"title": "Redirected"}}}

    async def unavailable_policy():
        raise OSError("policy storage unavailable")

    cdp.call = call
    session._policy_provider = unavailable_policy
    result = await session.read_page()
    assert result["outcome"] == "OK"
    assert result["data"]["title"] == "Redirected"
    assert (await session.run_script("1"))["outcome"] == "ERROR"


@pytest.mark.asyncio
async def test_scope_binding_is_task_local():
    session, _, audit = make_session()
    ready = asyncio.Event()

    async def first():
        session.bind_scope(turn_id="one", thread_id="thread")
        ready.set()
        await asyncio.sleep(0)
        return await session.navigate("https://x.example")

    async def second():
        await ready.wait()
        session.bind_scope(turn_id="two", thread_id="thread")
        return await session.navigate("https://x.example")

    a, b = await asyncio.gather(first(), second())
    assert a["outcome"] == "OK"
    assert b["outcome"] == "OK"
    assert {row["turn_id"] for row in audit} == {"one", "two"}


@pytest.mark.parametrize("url", ["https://host:invalid/", "https://host:99999/", "https://[broken/"])
def test_malformed_origin_is_rejected(url):
    assert origin_of(url) is None


def test_ipv6_origin_keeps_brackets():
    assert origin_of("http://[::1]:8080/x") == "http://[::1]:8080"


@pytest.mark.asyncio
async def test_cancelled_launch_reaps_process(tmp_path):
    from narranexus.platform.browser._browser_impl.runtime_launch import launch_session
    from tests.browser.test_runtime_launch import EXE, harness

    kwargs, recorder = harness()
    discovering = asyncio.Event()

    async def discover(**_):
        discovering.set()
        await asyncio.Event().wait()

    kwargs["discover"] = discover
    task = asyncio.create_task(launch_session(executable=EXE, root=tmp_path, agent_id="a",
                                            policy=BrowserPolicy(), **kwargs))
    await discovering.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert recorder["proc"].terminated


@pytest.mark.asyncio
async def test_failed_cdp_close_still_reaps_process(tmp_path):
    from narranexus.platform.browser._browser_impl.runtime_launch import launch_session
    from tests.browser.test_runtime_launch import EXE, harness

    kwargs, recorder = harness()
    session = await launch_session(executable=EXE, root=tmp_path, agent_id="a", policy=BrowserPolicy(), **kwargs)

    async def broken_close():
        raise RuntimeError("socket failed")

    recorder["sock"].close = broken_close
    with pytest.raises(RuntimeError):
        await session.close()
    assert recorder["proc"].terminated


@pytest.mark.asyncio
async def test_simultaneous_open_launches_once(monkeypatch):
    from narranexus.platform.browser._browser_impl import runtime_launch
    from tests.browser.test_browser_service import make_service

    service = make_service()
    launched = []

    async def launch(**_):
        await asyncio.sleep(0)
        session, _, _ = make_session()
        launched.append(session)
        return session

    monkeypatch.setattr(runtime_launch, "launch_session", launch)
    a, b = await asyncio.gather(service.open_session("a", policy=BrowserPolicy()),
                                service.open_session("a", policy=BrowserPolicy()))
    assert a[0] is b[0]
    assert len(launched) == 1
    await service.close()


@pytest.mark.asyncio
async def test_release_clears_held_inputs_before_agent_resumes():
    session, cdp, _ = make_session()
    await session.take_control("panel")
    await session.handle_user_input({"kind": "key", "type": "keyDown", "key": "Shift", "code": "ShiftLeft"}, "panel")
    await session.handle_user_input({"kind": "mouse", "type": "mousePressed", "button": "left", "buttons": 1, "x": 3, "y": 4}, "panel")
    await session.release_control("panel")
    released = [params["type"] for _, params in cdp.calls]
    assert "keyUp" in released and "mouseReleased" in released
    assert session.control.agent_may_act()


@pytest.mark.parametrize("event", [
    {"kind": "key", "type": [], "key": "a"},
    {"kind": "mouse", "type": {}, "x": 1, "y": 1},
])
def test_non_string_input_types_are_ignored(event):
    assert input_events_for(event) == []


def test_explicit_keyboard_text_and_virtual_code_are_preserved():
    params = input_events_for({"kind": "key", "type": "keyDown", "key": "Enter",
                               "windowsVirtualKeyCode": 13, "text": "\r"})[0][1]
    assert params["text"] == "\r"
    assert params["windowsVirtualKeyCode"] == 13


@pytest.mark.asyncio
async def test_service_shutdown_rejects_new_launches_and_is_shared(monkeypatch):
    from narranexus.platform.browser._browser_impl import runtime_launch
    from tests.browser.test_browser_service import make_service

    service = make_service()
    started, finish = asyncio.Event(), asyncio.Event()
    closed = []
    launched = []

    async def close_agent(agent_id):
        closed.append(agent_id)
        started.set()
        await finish.wait()

    async def launch(**_):
        session, _, _ = make_session()
        launched.append(session)
        return session

    monkeypatch.setattr(service, "close_session", close_agent)
    monkeypatch.setattr(runtime_launch, "launch_session", launch)
    service.register_session("first", make_session()[0])
    shutdown = asyncio.create_task(service.close())
    await started.wait()
    try:
        session, refusal = await service.open_session("late", policy=BrowserPolicy())
        assert session is None
        assert refusal["outcome"] == "ERROR"
        assert not launched
    finally:
        finish.set()
        await shutdown
    await service.close()
    assert closed == ["first"]
    assert (await service.open_session("after", policy=BrowserPolicy()))[0] is None


@pytest.mark.asyncio
async def test_service_shutdown_drains_every_session_despite_errors(monkeypatch):
    from tests.browser.test_browser_service import make_service

    service = make_service()
    closed = []

    async def fail_close(agent_id):
        closed.append(agent_id)
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(service, "close_session", fail_close)
    for agent_id in ("one", "two", "three"):
        service.register_session(agent_id, make_session()[0])
    with pytest.raises(ExceptionGroup):
        await service.close()
    assert set(closed) == {"one", "two", "three"}


@pytest.mark.asyncio
async def test_service_shutdown_cancellation_still_finishes_cleanup(monkeypatch):
    from tests.browser.test_browser_service import make_service

    service = make_service()
    started, finish, done = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def close_agent(agent_id):
        started.set()
        await finish.wait()
        done.set()

    monkeypatch.setattr(service, "close_session", close_agent)
    service.register_session("first", make_session()[0])
    shutdown = asyncio.create_task(service.close())
    await started.wait()
    shutdown.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    assert done.is_set()
