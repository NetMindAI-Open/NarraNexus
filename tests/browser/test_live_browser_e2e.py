"""
@file_name: test_live_browser_e2e.py
@date: 2026-09-23
@description: Opt-in real Chromium regression using an isolated local page and profile.

Run with NARRANEXUS_RUN_BROWSER_E2E=1 after explicitly installing the runtime.
The test never downloads Chromium or changes a user's browser profile.
"""
from __future__ import annotations

import asyncio
import base64
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import parse_qs

import pytest
from PIL import Image

from narranexus.platform.browser._browser_impl.locate import locate_executable
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy
from narranexus.platform.browser._browser_impl.runtime_launch import launch_session
from narranexus.platform.browser._browser_impl.selection import locate_system_executable

pytestmark = pytest.mark.skipif(
    os.environ.get("NARRANEXUS_RUN_BROWSER_E2E") != "1",
    reason="Explicit opt-in required for installed Chromium",
)

PAGE = b'''<!doctype html><html lang="en"><title>Browser integration fixture</title>
<style>body{font:24px sans-serif;background:#fff;color:#171717;margin:32px}input{font:24px sans-serif}
.band{height:1800px;background:linear-gradient(#edffff,#d8efda)}</style>
<h1>Browser integration fixture</h1><input id="entry" aria-label="Entry">
<button id="count" onclick="this.textContent=Number(this.textContent)+1">0</button>
<a id="new-tab" href="/popup" target="_blank">Open a second page</a>
<button id="popup" onclick="window.open('/popup?window', 'fixture-popup', 'width=600,height=500')">Open popup</button>
<button id="rename" onclick="document.title='Updated title';history.pushState({},'', '/updated')">Update title and URL</button>
<div class="band">Scroll fixture</div><p>End</p></html>'''

LOGIN_PAGE = b'''<!doctype html><html lang="en"><title>Browser login fixture</title>
<form method="post" action="/login"><label>User <input name="user" id="username"></label>
<label>Password <input name="password" type="password"></label><button>Sign in</button></form></html>'''


@pytest.fixture
def browser_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if self.path == "/login":
                authenticated = "browser_login=retained" in self.headers.get("Cookie", "")
                self.wfile.write(b"<h1>Signed in as browser-test</h1>" if authenticated else LOGIN_PAGE)
            elif self.path.startswith("/popup"):
                self.wfile.write(b'''<!doctype html><title>Popup fixture</title><h1>Second page</h1>
<input id="popup-input"><button id="close" onclick="window.close()">Close</button>''')
            else:
                self.wfile.write(PAGE)

        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
            if values != {"user": ["browser-test"], "password": ["fixture-password"]}:
                self.send_error(403)
                return
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "browser_login=retained; HttpOnly; SameSite=Lax; Max-Age=3600; Path=/")
            self.end_headers()

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


async def eventually(session, expression, expected):
    result = None
    try:
        async with asyncio.timeout(10):
            while True:
                result = await session.run_script(expression)
                if result.get("data") == expected:
                    return result
                await asyncio.sleep(0.05)
    except TimeoutError:
        pytest.fail(f"Page did not reach {expression} == {expected!r}: {result!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("headless", [True, False], ids=["headless", "headed"])
async def test_real_popup_discovery_switching_streams_and_closure(tmp_path, browser_site, headless):
    executable = locate_system_executable()
    if executable is None:
        pytest.skip("Installed stable Chrome is unavailable")
    session = await launch_session(executable=executable, root=tmp_path, agent_id="pages_e2e",
                                   policy=BrowserPolicy(), headless=headless)

    async def wait_for(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                await asyncio.sleep(0.02)

    async def title(expected):
        async with asyncio.timeout(10):
            while True:
                result = await session.read_page()
                if result.get("data", {}).get("title") == expected:
                    await wait_for(lambda: session.pages.current.title == expected)
                    return
                await asyncio.sleep(0.02)

    try:
        assert (await session.navigate(browser_site))["ok"]
        await title("Browser integration fixture")
        original = session.pages.active_id
        assert len(session.pages.items) == 1
        assert (await session.act("click", selector="#new-tab"))["ok"]
        await wait_for(lambda: len(session.pages.items) == 2)
        popup = session.pages.active_id
        assert popup != original
        await title("Popup fixture")
        frames: asyncio.Queue = asyncio.Queue()
        session.subscribe(frames.put_nowait, page_id=original)
        session.subscribe(frames.put_nowait, page_id=popup)
        await asyncio.gather(
            session.start_stream(page_id=original),
            session.resize_viewport(404, 563, "viewer", page_id=original),
        )
        viewport = await session.pages.get(original).cdp.call("Runtime.evaluate", {
            "expression": "[innerWidth, innerHeight]", "returnByValue": True,
        })
        assert viewport["result"]["value"] == [404, 563]
        await session.start_stream(page_id=popup)
        seen = set()
        async with asyncio.timeout(10):
            while len(seen) < 2:
                frame = await frames.get()
                assert len(base64.b64decode(frame["data"])) > 500
                seen.add(frame["page_id"])
        assert seen == {original, popup}
        await session.resize_viewport(417, 579, "viewer", page_id=original)
        async with asyncio.timeout(10):
            while True:
                frame = await frames.get()
                metadata = frame["meta"]
                if (frame["page_id"] == original and metadata.get("deviceWidth") == 417
                        and metadata.get("deviceHeight") == 579):
                    with Image.open(BytesIO(base64.b64decode(frame["data"]))) as picture:
                        assert picture.size == (417, 579)
                    break
        while not frames.empty():
            frames.get_nowait()
        assert (await session.select_page(original))["ok"]
        assert (await session.act("click", selector="#count"))["ok"]
        async with asyncio.timeout(10):
            while (await frames.get())["page_id"] != original:
                pass
        assert (await session.act("click", selector="#rename"))["ok"]
        await title("Updated title")
        await wait_for(lambda: session.pages.current.url == browser_site + "/updated")
        assert await session.take_control("viewer", page_id=original)
        assert await session.select_user_page(popup, "viewer")
        await session.handle_user_input({"kind": "mouse", "type": "mousePressed", "x": 60, "y": 90}, "viewer", page_id=original)
        assert await session.release_control("viewer")
        assert (await session.act("fill", selector="#popup-input", text="Popup input"))["ok"]
        assert (await session.select_page(original))["ok"]
        await title("Updated title")
        assert (await session.act("click", selector="#popup"))["ok"]
        await wait_for(lambda: len(session.pages.items) == 3)
        window_id = session.pages.active_id
        await title("Popup fixture")
        assert (await session.act("click", selector="#close"))["ok"]
        await wait_for(lambda: window_id not in session.pages.items)
        assert session.is_open
        assert (await session.navigate(browser_site, new_page=True))["ok"]
        assert len(session.pages.items) == 3
        for page_id in list(session.pages.items):
            assert (await session.close_page(page_id))["ok"]
        assert session.is_open and len(session.pages.items) == 1
        assert session.pages.current.url == "about:blank"
        assert (await session.navigate(browser_site))["ok"]
        await title("Browser integration fixture")
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_verdict", [None, "ask", "deny"])
async def test_unconfigured_agent_browses_and_acts_without_site_approvals(tmp_path, browser_site, legacy_verdict):
    executable = locate_system_executable()
    if executable is None:
        pytest.skip("Installed stable Chrome is unavailable")
    policy = BrowserPolicy.from_dict({
        "default_origin_policy": {"access": legacy_verdict} if legacy_verdict else {},
        "origins": {browser_site: {"access": "deny"}},
        "denials": [[browser_site, "access", "turn:turn_0"], [browser_site, "access", "thread:thread_0"]],
    })
    session = await launch_session(
        executable=executable, root=tmp_path, agent_id="unconfigured_e2e", policy=policy,
        headless=True, turn_id="first", thread_id="first",
    )
    try:
        for index, origin in enumerate((browser_site, browser_site.replace("127.0.0.1", "localhost"))):
            session.bind_scope(turn_id=f"turn_{index}", thread_id=f"thread_{index}")
            assert (await session.navigate(origin))["outcome"] == "OK"
            async with asyncio.timeout(10):
                while True:
                    page = await session.read_page()
                    assert page["outcome"] == "OK"
                    if page.get("data", {}).get("title") == "Browser integration fixture":
                        break
                    await asyncio.sleep(0.05)
            assert (await session.act("fill", selector="#entry", text="No site approval"))["outcome"] == "OK"
            assert (await session.capture_screenshot())["outcome"] == "OK"
            assert (await session.run_script("document.title"))["outcome"] == "REJECTED"
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("headless", [True, False], ids=["headless", "headed"])
async def test_user_creates_navigates_and_closes_tabs_then_agent_continues(tmp_path, browser_site, headless):
    executable = locate_system_executable()
    if executable is None:
        pytest.skip("Installed stable Chrome is unavailable")
    session = await launch_session(executable=executable, root=tmp_path, agent_id="manual_e2e",
                                   policy=BrowserPolicy(), headless=headless)
    try:
        assert (await session.navigate(browser_site))["ok"]
        original = session.pages.active_id
        assert await session.take_control("owner")
        page_id = await session.new_user_page("owner")
        assert page_id and page_id != original
        assert len(session.pages.items) == 2
        assert not await session.close_user_page(original, "spectator")
        assert await session.navigate_user_page(page_id, browser_site + "/popup", "owner")
        await session.pages.flush()
        async with asyncio.timeout(10):
            while session.pages.get(page_id).title != "Popup fixture":
                await asyncio.sleep(0.02)
        assert await session.release_control("owner")
        assert (await session.act("fill", selector="#popup-input", text="Handed back"))["ok"]
        assert await session.take_control("owner")
        assert await session.close_user_page(page_id, "owner")
        assert session.pages.active_id == original
        assert await session.close_user_page(original, "owner")
        assert session.is_open and len(session.pages.items) == 1
        assert session.pages.current.url == "about:blank"
        assert await session.navigate_user_page(session.pages.active_id, browser_site, "owner")
        assert await session.release_control("owner")
        async with asyncio.timeout(10):
            while (await session.read_page()).get("data", {}).get("title") != "Browser integration fixture":
                await asyncio.sleep(0.02)
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["managed", "system"])
@pytest.mark.parametrize("headless", [True, False], ids=["headless", "headed"])
async def test_real_frames_takeover_text_scroll_evidence_and_persistent_profile(
    tmp_path, browser_site, source, headless,
):
    executable = locate_system_executable() if source == "system" else locate_executable()
    if source == "system" and executable is None:
        pytest.skip("Installed stable Chrome is unavailable")
    assert executable is not None, "Install Chromium explicitly before this test"
    policy = BrowserPolicy(origins={browser_site: OriginPolicy(full_cdp_access="allow")})
    frames: asyncio.Queue = asyncio.Queue()
    audit = []
    processes = []

    async def spawn(argv):
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        processes.append(process)
        return process

    options = dict(executable=executable, root=tmp_path, agent_id="browser_e2e",
                   policy=policy, headless=headless, turn_id="e2e_turn", thread_id="e2e_thread",
                   audit=audit.append, spawn=spawn)
    session = await launch_session(**options)
    process = processes[-1]
    try:
        assert (await session.navigate(browser_site))["outcome"] == "OK"
        await eventually(session, "document.title", "Browser integration fixture")
        await eventually(session, "navigator.userAgent.includes('HeadlessChrome/')", headless)
        session.subscribe(frames.put_nowait)
        await session.start_stream()
        frame = await asyncio.wait_for(frames.get(), 10)
        assert base64.b64decode(frame["data"]).startswith(b"\xff\xd8")
        assert len(frame["data"]) > 1000
        assert (await session.run_script("document.querySelector('#entry').focus(); true"))["outcome"] == "OK"
        assert await session.take_control("test-panel")
        assert not await session.take_control("other-panel")
        pending = asyncio.create_task(session.run_script("document.querySelector('#entry').value"))
        await asyncio.sleep(0.05)
        assert not pending.done(), "Agent action must wait for explicit handback"
        await session.handle_user_input({"kind": "text", "text": "ignored"}, "other-panel")
        await session.handle_user_input({"kind": "key", "type": "keyDown", "key": "a", "code": "KeyA"}, "test-panel")
        await session.handle_user_input({"kind": "text", "text": "\u4e2d\u6587 paste"}, "test-panel")
        await session.handle_user_input({"kind": "wheel", "x": 600, "y": 500, "deltaY": 500}, "test-panel")
        assert await session.release_control("test-panel")
        assert (await asyncio.wait_for(pending, 5))["data"] == "a\u4e2d\u6587 paste"
        await eventually(session, "scrollY > 0", True)
        capture = await session.capture_screenshot()
        assert capture["outcome"] == "OK"
        assert capture["mime_type"] == "image/png"
        assert base64.b64decode(capture["data"]).startswith(b"\x89PNG\r\n\x1a\n")
        await session.run_script("document.cookie='browser_e2e=retained; Max-Age=3600; Path=/'; true")
    finally:
        await session.close()
    assert process.returncode is not None, "Closing a session must reap Chromium"
    assert not session.is_open

    restored = await launch_session(**options)
    try:
        await restored.navigate(browser_site)
        await eventually(restored, "document.cookie.includes('browser_e2e=retained')", True)
    finally:
        await restored.close()
    assert any(item.get("event") == "closed" for item in audit)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["managed", "system"])
@pytest.mark.parametrize("headless", [True, False], ids=["headless", "headed"])
async def test_human_login_retains_server_cookie_without_script_permission(
    tmp_path, browser_site, source, headless,
):
    executable = locate_system_executable() if source == "system" else locate_executable()
    if source == "system" and executable is None:
        pytest.skip("Installed stable Chrome is unavailable")
    assert executable is not None, "Install Chromium explicitly before this test"
    policy = BrowserPolicy(origins={browser_site: OriginPolicy()})
    audit = []
    options = dict(executable=executable, root=tmp_path, agent_id="browser_login_e2e",
                   policy=policy, headless=headless, turn_id="login_turn", thread_id="login_thread",
                   audit=audit.append)

    async def read_until(session, expected):
        async with asyncio.timeout(10):
            while True:
                result = await session.read_page()
                if expected in result.get("data", {}).get("text", ""):
                    return result
                await asyncio.sleep(0.05)

    session = await launch_session(**options)
    try:
        assert (await session.navigate(browser_site + "/login"))["outcome"] == "OK"
        await read_until(session, "Password")
        assert (await session.act("click", selector="#username"))["outcome"] == "OK"
        assert await session.take_control("login-panel")
        await session.handle_user_input({"kind": "text", "text": "browser-test"}, "login-panel")
        for kind in ("keyDown", "keyUp"):
            await session.handle_user_input({"kind": "key", "type": kind, "key": "Tab", "code": "Tab"}, "login-panel")
        await session.handle_user_input({"kind": "text", "text": "fixture-password"}, "login-panel")
        for kind in ("keyDown", "keyUp"):
            await session.handle_user_input({"kind": "key", "type": kind, "key": "Enter", "code": "Enter"}, "login-panel")
        assert await session.release_control("login-panel")
        await read_until(session, "Signed in as browser-test")
        assert (await session.run_script("document.cookie"))["outcome"] == "REJECTED"
    finally:
        await session.close()

    restored = await launch_session(**{**options, "headless": not headless})
    try:
        assert (await restored.navigate(browser_site + "/login"))["outcome"] == "OK"
        await read_until(restored, "Signed in as browser-test")
    finally:
        await restored.close()
    assert "fixture-password" not in repr(audit)


@pytest.mark.asyncio
async def test_normal_service_uses_persisted_mode_without_a_diagnostic_entry_point(tmp_path, browser_site, monkeypatch):
    from narranexus.platform.browser._browser_impl.selection import save_mode, save_source
    from narranexus.platform.browser.browser_service import BrowserService

    if locate_system_executable() is None:
        pytest.skip("Installed stable Chrome is unavailable")
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", str(tmp_path))
    save_source("system")
    save_mode("headed")
    policy = BrowserPolicy(origins={browser_site: OriginPolicy(full_cdp_access="allow")})
    service = BrowserService()
    try:
        session, refusal = await service.open_session("mode_e2e", policy=policy)
        assert session is not None and refusal is None
        await session.navigate(browser_site)
        await eventually(session, "navigator.userAgent.includes('HeadlessChrome/')", False)
        await session.run_script("document.cookie='mode_e2e=retained; Max-Age=3600; Path=/'; true")
        save_mode("headless")
        assert (await service.open_session("mode_e2e", policy=policy))[0] is session
        await service.close_session("mode_e2e")
        restored, refusal = await service.open_session("mode_e2e", policy=policy)
        assert restored is not None and refusal is None
        await restored.navigate(browser_site)
        await eventually(restored, "navigator.userAgent.includes('HeadlessChrome/')", True)
        await eventually(restored, "document.cookie.includes('mode_e2e=retained')", True)
    finally:
        await service.close()


VISUAL_PAGE = b'''<!doctype html><html lang="en"><title>Visual fixture</title>
<style>body{margin:0;font:20px sans-serif}#board{display:block;margin:120px 0 0 240px}</style>
<h2 id="status">waiting</h2>
<canvas id="board" width="400" height="300"></canvas>
<div style="height:1600px"></div>
<script>
const board = document.getElementById('board'), g = board.getContext('2d');
g.fillStyle = '#f4f4f4'; g.fillRect(0, 0, 400, 300);
g.fillStyle = '#e00000'; g.fillRect(290, 190, 40, 40);
board.addEventListener('click', event => {
  const box = board.getBoundingClientRect(), x = event.clientX - box.left, y = event.clientY - box.top;
  document.getElementById('status').textContent =
    x >= 290 && x < 330 && y >= 190 && y < 230 ? 'hit target' : 'missed at ' + Math.round(x) + ',' + Math.round(y);
});
</script></html>'''


def red_centroid(data: str) -> tuple[float, float]:
    """Locate the red canvas square in a screenshot by pixels alone.

    Red dominance, not an exact value: headed Chrome on macOS renders in the
    display's color profile, so #e00000 is captured as about (206, 44, 30)
    while headless captures it verbatim.
    """
    image = Image.open(BytesIO(base64.b64decode(data))).convert("RGB")
    points = [(x, y) for y in range(image.height) for x in range(image.width)
              if (lambda p: p[0] > 150 and p[0] > 3 * p[1] and p[0] > 3 * p[2])(image.getpixel((x, y)))]
    assert points, "red target not visible in the observed image"
    return sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points)


@pytest.mark.asyncio
@pytest.mark.parametrize("headless", [True, False], ids=["headless", "headed"])
async def test_visual_observation_drives_canvas_clicks_and_rejects_stale_views(tmp_path, headless):
    """A canvas target has no DOM handle: only the image shows where it is.
    Viewport and magnified-crop observations must both map image pixels to
    the right viewport point on a real Chrome, whatever its device ratio."""
    executable = locate_system_executable()
    if executable is None:
        pytest.skip("Installed stable Chrome is unavailable")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(VISUAL_PAGE)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    session = await launch_session(executable=executable, root=tmp_path, agent_id="visual_e2e",
                                   policy=BrowserPolicy(), headless=headless)

    async def status():
        return (await session.read_page(selector="#status"))["data"]["text"].strip()

    try:
        assert (await session.navigate(f"http://127.0.0.1:{server.server_port}"))["ok"]
        view = await session.observe_page()
        assert view["outcome"] == "OK", view
        assert max(view["image"]["width"], view["image"]["height"]) <= 1568
        x, y = red_centroid(view["data"])
        assert (await session.act("click", observation_id=view["observation_id"], x=x, y=y))["outcome"] == "OK"
        assert await status() == "hit target"
        stale = await session.act("click", observation_id=view["observation_id"], x=x, y=y)
        assert stale["outcome"] == "ERROR" and "browser_look" in stale["message"]

        await session.navigate(f"http://127.0.0.1:{server.server_port}")
        crop = await session.observe_page(selector="#board", scale=2)
        assert crop["outcome"] == "OK", crop
        assert crop["region"]["width"] == 400 and crop["image"]["width"] > 400
        x, y = red_centroid(crop["data"])
        assert (await session.act("click", observation_id=crop["observation_id"], x=x, y=y))["outcome"] == "OK"
        assert await status() == "hit target"

        view = await session.observe_page()
        assert (await session.act("scroll", delta_y=200))["outcome"] == "OK"
        moved = await session.act("click", observation_id=view["observation_id"], x=10, y=10)
        assert moved["outcome"] == "ERROR"
    finally:
        await session.close()
        server.shutdown()
        server.server_close()
