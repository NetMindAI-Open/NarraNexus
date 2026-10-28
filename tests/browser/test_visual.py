"""Regression coverage for visual observations and image-coordinate actions."""
from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO

import pytest
from PIL import Image

from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
from narranexus.platform.browser._browser_impl.session import BrowserSession


class VisualCdp:
    is_open = True

    def __init__(self):
        self.calls = []
        self.state = {
            "url": "https://example.com/chart", "title": "Chart", "document_id": 1234,
            "viewport": {"width": 800, "height": 600, "scroll_x": 0, "scroll_y": 200, "dpr": 2},
            "region": {"x": 100, "y": 50, "width": 200, "height": 100},
        }

    async def call(self, method, params=None, **kwargs):
        params = params or {}
        self.calls.append((method, params))
        if method == "Runtime.evaluate":
            expression = params["expression"]
            if expression == "location.href":
                value = self.state["url"]
            elif "document_id" in expression:
                value = json.loads(json.dumps(self.state))
            else:
                arguments = json.loads(expression.rsplit(")(", 1)[1][:-1])
                value = {"x": arguments["x"], "y": arguments["y"]}
            return {"result": {"value": value}}
        if method == "Page.captureScreenshot":
            clip = params["clip"]
            buffer = BytesIO()
            Image.new("RGB", (int(clip["width"] * clip["scale"] * 2),
                              int(clip["height"] * clip["scale"] * 2)), "red").save(buffer, "PNG")
            return {"data": base64.b64encode(buffer.getvalue()).decode()}
        return {}


def visual_session():
    cdp, audit = VisualCdp(), []
    session = BrowserSession(cdp=cdp, policy=BrowserPolicy(), audit=audit.append,
                             turn_id="turn", thread_id="thread")
    return session, cdp, audit


@pytest.mark.asyncio
async def test_crop_retina_image_coordinates_map_to_viewport_and_are_consumed():
    session, cdp, audit = visual_session()
    shot = await session.observe_page(selector="#chart", scale=2)
    assert shot["outcome"] == "OK"
    assert shot["image"] == {"width": 800, "height": 400, "mime_type": "image/png"}
    capture = next(params for method, params in cdp.calls if method == "Page.captureScreenshot")
    assert capture["clip"] == {"x": 100, "y": 250, "width": 200, "height": 100, "scale": 2}
    result = await session.act("click", observation_id=shot["observation_id"], x=400, y=200)
    assert result["outcome"] == "OK"
    click = next(params for method, params in cdp.calls
                 if method == "Input.dispatchMouseEvent" and params["type"] == "mousePressed")
    assert (click["x"], click["y"]) == (200, 100)
    again = await session.act("click", observation_id=shot["observation_id"], x=400, y=200)
    assert again["outcome"] == "ERROR"
    assert shot["data"] not in json.dumps(audit)


@pytest.mark.asyncio
@pytest.mark.parametrize("scale", [1, 3])
async def test_large_retina_viewport_is_capped_to_model_resolution(scale):
    """Providers downscale anything past ~1568px / ~1.15MP anyway; capturing
    more only costs bandwidth, memory and payload size."""
    session, cdp, _ = visual_session()
    cdp.state["viewport"] = {"width": 1600, "height": 1000, "scroll_x": 0, "scroll_y": 0, "dpr": 2}
    cdp.state["region"] = {"x": 0, "y": 0, "width": 1600, "height": 1000}
    shot = await session.observe_page(scale=scale)
    assert shot["outcome"] == "OK"
    width, height = shot["image"]["width"], shot["image"]["height"]
    assert max(width, height) <= 1568
    assert width * height <= 1_150_000
    assert width > 1200  # capped, not collapsed
    capture = next(params for method, params in cdp.calls if method == "Page.captureScreenshot")
    assert capture["clip"]["scale"] < 1
    result = await session.act("click", observation_id=shot["observation_id"], x=width / 2, y=height / 2)
    assert result["outcome"] == "OK"
    click = next(params for method, params in cdp.calls
                 if method == "Input.dispatchMouseEvent" and params["type"] == "mousePressed")
    assert click["x"] == pytest.approx(800, abs=1)
    assert click["y"] == pytest.approx(500, abs=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["scroll", "resize", "reload", "url", "scope", "tab", "takeover"])
async def test_visual_actions_refuse_changed_context(change):
    session, cdp, _ = visual_session()
    shot = await session.observe_page()
    if change == "scroll":
        cdp.state["viewport"]["scroll_y"] += 1
    elif change == "resize":
        cdp.state["viewport"]["width"] += 1
    elif change == "reload":
        cdp.state["document_id"] += 1
    elif change == "url":
        cdp.state["url"] += "?changed"
    elif change == "scope":
        session.bind_scope(turn_id="other", thread_id="thread")
    elif change == "tab":
        session.pages.add("other", VisualCdp())
        session.pages.select("other")
    else:
        await session.take_control("human")
        await session.release_control("human")
    result = await session.act("click", observation_id=shot["observation_id"], x=10, y=10)
    assert result["outcome"] == "ERROR"
    assert "browser_look" in result["message"]
    assert not any(method == "Input.dispatchMouseEvent" for method, _ in cdp.calls)


@pytest.mark.asyncio
async def test_visual_capture_waits_for_human_and_rejects_non_web_pages():
    session, cdp, _ = visual_session()
    await session.take_control("human")
    task = asyncio.create_task(session.observe_page())
    await asyncio.sleep(0)
    assert not task.done()
    assert not cdp.calls
    await session.release_control("human")
    assert (await task)["outcome"] == "OK"
    cdp.state["url"] = "file:///private/example"
    cdp.calls.clear()
    assert (await session.observe_page())["outcome"] == "REJECTED"
    assert all(method != "Page.captureScreenshot" for method, _ in cdp.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"scale": float("nan")}, {"scale": 0}, {"scale": 9}, {"selector": ""},
    {"x": 0}, {"x": -1, "y": 0, "width": 10, "height": 10},
    {"x": 0, "y": 0, "width": 0, "height": 10},
    {"selector": "#chart", "x": 0, "y": 0, "width": 10, "height": 10},
])
async def test_invalid_capture_requests_do_not_reach_screenshot(kwargs):
    session, cdp, _ = visual_session()
    assert (await session.observe_page(**kwargs))["outcome"] == "ERROR"
    assert all(method != "Page.captureScreenshot" for method, _ in cdp.calls)
