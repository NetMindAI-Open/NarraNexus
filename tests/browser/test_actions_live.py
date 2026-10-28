"""
@file_name: test_actions_live.py
@date: 2026-09-23
@description: Opt-in Chromium validation of fixed actions without script privileges.
"""
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from narranexus.platform.browser._browser_impl.locate import locate_executable
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy
from narranexus.platform.browser._browser_impl.runtime_launch import launch_session

pytestmark = pytest.mark.skipif(
    os.environ.get("NARRANEXUS_RUN_BROWSER_E2E") != "1", reason="Explicit Chromium opt-in required",
)

PAGE = b'''<!doctype html><title>Fixed actions fixture</title>
<style>body{margin:32px}input,button,select{font:24px sans-serif}#long{height:2400px}</style>
<form onsubmit="event.preventDefault(); document.querySelector('#result').textContent=q.value">
<input id="q" name="q" oninput="this.dataset.seen=this.value"><button>Search</button></form>
<select id="pick" onchange="this.dataset.seen=this.value"><option value="a">A</option>
<option value="b">B</option><option value="c" disabled>C</option></select>
<div id="edit" contenteditable="true" style="height:32px">Editable</div>
<button id="disabled" disabled>Disabled</button><input id="file" type="file">
<input id="readonly" readonly><output id="result"></output><div id="long">Scroll</div>'''


@pytest.fixture
def action_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_real_access_approved_forms_without_arbitrary_scripts(tmp_path, action_site):
    executable = locate_executable()
    assert executable, "Install Chromium before running the opt-in test"
    session = await launch_session(
        executable=executable, root=tmp_path, agent_id="fixed_actions_test", headless=True,
        policy=BrowserPolicy(origins={action_site: OriginPolicy()}),
        turn_id="turn", thread_id="thread", audit=lambda _: None,
    )

    # The observer reads test state directly; tool callers still have no script
    # privilege. This proves the gestures changed Chromium, not just a mock.
    async def observe(expression):
        response = await session._cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return response.get("result", {}).get("value")

    async def eventually(expression, expected):
        async with asyncio.timeout(10):
            while await observe(expression) != expected:
                await asyncio.sleep(0.02)

    async def act(action, **kwargs):
        result = await session.act(action, **kwargs)
        assert result["outcome"] == "OK", result

    try:
        assert (await session.navigate(action_site))["outcome"] == "OK"
        await eventually("document.title", "Fixed actions fixture")
        assert (await session.run_script("document.title"))["outcome"] == "REJECTED"
        text = "query \u4e2d\u6587 '); window.injected=true; //"
        await act("fill", selector="#q", text=text)
        assert await observe("q.value") == text
        assert await observe("q.dataset.seen") == text
        assert await observe("window.injected === undefined") is True
        await act("click", selector="button")
        await eventually("document.querySelector('#result').textContent", text)
        await act("fill", selector="#q", text="enter")
        await act("press", selector="#q", key="End")
        await act("press", key="!")
        await act("press", key="Enter")
        await eventually("document.querySelector('#result').textContent", "enter!")
        await act("select", selector="#pick", value="b")
        assert await observe("document.querySelector('#pick').dataset.seen") == "b"
        await act("fill", selector="#edit", text="Editable value")
        assert await observe("document.querySelector('#edit').textContent") == "Editable value"
        for action, kwargs in [
            ("select", {"selector": "#pick", "value": "c"}),
            ("fill", {"selector": "#file", "text": "/tmp/private"}),
            ("fill", {"selector": "#readonly", "text": "ignored"}),
            ("click", {"selector": "#disabled"}), ("click", {"selector": "#missing"}),
        ]:
            assert (await session.act(action, **kwargs))["outcome"] == "ERROR"
        await act("scroll", delta_y=400)
        await eventually("scrollY > 0", True)
        assert (await session.run_script("document.title"))["outcome"] == "REJECTED"
        frames = asyncio.Queue()
        session.subscribe(frames.put_nowait)
        await session.start_stream()
        assert await session.resize_viewport(392, 617, "panel")
        await eventually("[innerWidth, innerHeight, devicePixelRatio].join(',')", "392,617,1")
        async with asyncio.timeout(10):
            while True:
                frame = await frames.get()
                if frame["meta"].get("deviceWidth") == 392 and frame["meta"].get("deviceHeight") == 617:
                    break
        assert frame["data"]
        assert await session.take_control("owner")
        assert not await session.resize_viewport(600, 700, "spectator")
        assert await observe("innerWidth") == 392
        assert await session.resize_viewport(600, 700, "owner")
        await eventually("[innerWidth, innerHeight].join(',')", "600,700")
        await session.release_control("owner")
    finally:
        await session.close()
