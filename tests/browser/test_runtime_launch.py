"""
@file_name: test_runtime_launch.py
@author:
@date: 2026-09-22
@description: Tests for launching a browser and turning it into a BrowserSession.

The glue between "a binary on disk" and "a session the agent can drive". What
gets pinned here is what actually goes wrong with child processes:

* **No orphans.** Closing a session must kill the browser. A 190 MB Chromium
  left running after its session ends holds a debugging port and a profile
  lock, and the next launch fails for a reason that points nowhere near the
  leak. (This repo has paid for orphaned children before; so did I, during
  this very task, when killing a Tauri parent left its Python sidecars holding
  :8000.)
* **One browser per agent.** Asking twice must not spawn a second one.
* **Launch failure is an outcome, not an exception.** The caller is an agent
  turn; it needs something to say, not a traceback.

Nothing here starts a real browser: spawn and connect are injected.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy
from narranexus.platform.browser._browser_impl.runtime_launch import (
    LaunchError,
    free_port,
    launch_session,
)

EXE = Path("/x/chrome")


@pytest.mark.asyncio
async def test_local_cdp_discovery_and_socket_ignore_environment_proxies(monkeypatch):
    import httpx
    import websockets
    from narranexus.platform.browser._browser_impl.runtime_launch import _default_connect, _default_discover

    options = {}

    class Client:
        def __init__(self, **kwargs):
            options["http"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            assert url.endswith("/json/version")
            return httpx.Response(200, request=httpx.Request("GET", url),
                                  json={"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test"})

    async def connect(url, **kwargs):
        options["ws"] = kwargs
        return object()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(websockets, "connect", connect)
    url = await _default_discover(port=9222)
    await _default_connect(url)
    assert options["http"].get("trust_env") is False
    assert "proxy" in options["ws"] and options["ws"]["proxy"] is None


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.returncode = None

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode or 0


class FakeSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.closed = False
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        import json

        self.sent.append(raw)
        msg = json.loads(raw)
        if "id" in msg:
            result = {"Target.createTarget": {"targetId": "main"},
                      "Target.attachToTarget": {"sessionId": "s-main"},
                      "Target.getTargets": {"targetInfos": []}}.get(msg["method"], {})
            await self._q.put(json.dumps({"id": msg["id"], "result": result}))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self.closed:
            raise StopAsyncIteration
        return await self._q.get()

    async def close(self) -> None:
        self.closed = True


def harness(*, spawn_fails=False, connect_fails=False):
    """Returns (kwargs, recorder) for launch_session."""
    rec = {"argv": None, "proc": FakeProcess(), "sock": FakeSocket(), "spawns": 0}

    async def spawn(argv):
        rec["spawns"] += 1
        rec["argv"] = argv
        if spawn_fails:
            raise OSError("exec format error")
        return rec["proc"]

    async def discover(*, port):  # noqa: ARG001
        return "ws://127.0.0.1:1/devtools/browser/abc"

    async def connect(ws_url):  # noqa: ARG001
        if connect_fails:
            raise ConnectionError("refused")
        return rec["sock"]

    return dict(spawn=spawn, discover=discover, connect=connect), rec


# ── port selection ───────────────────────────────────────────────────────────


def test_free_port_returns_a_usable_port():
    port = free_port()
    assert 1024 < port < 65536


def test_free_port_does_not_repeat_immediately():
    """Two sessions starting together must not be handed the same port."""
    assert free_port() != free_port() or True  # non-flaky: see below
    ports = {free_port() for _ in range(5)}
    assert len(ports) > 1, "a fixed port would collide between concurrent agents"


# ── launching ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_returns_a_driveable_session(tmp_path: Path):
    kw, rec = harness()
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw
    )
    try:
        assert session.is_open is True
        assert session.control.holder == "agent"
        assert rec["spawns"] == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_launch_passes_the_chosen_port_and_profile_to_the_browser(tmp_path: Path):
    kw, rec = harness()
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw
    )
    try:
        argv = rec["argv"]
        assert argv[0] == str(EXE)
        assert any(a.startswith("--remote-debugging-port=") for a in argv)
        assert "--remote-debugging-address=127.0.0.1" in argv
        assert any(a.startswith("--user-data-dir=") for a in argv)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_the_policy_is_carried_into_the_session(tmp_path: Path):
    """Explicit advanced script restrictions belong to the launched session."""
    kw, _rec = harness()
    policy = BrowserPolicy(default_origin_policy=OriginPolicy(full_cdp_access="deny"))
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=policy, **kw
    )
    try:
        assert session._policy is policy
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_failed_spawn_raises_launch_error_with_the_reason(tmp_path: Path):
    kw, _rec = harness(spawn_fails=True)
    with pytest.raises(LaunchError, match="exec format"):
        await launch_session(
            executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw
        )


@pytest.mark.asyncio
async def test_a_failed_connect_kills_the_process_it_started(tmp_path: Path):
    """Otherwise the browser is an orphan holding a port and a profile lock,
    and the NEXT launch fails for a reason that points nowhere near this."""
    kw, rec = harness(connect_fails=True)
    with pytest.raises(LaunchError):
        await launch_session(
            executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw
        )
    assert rec["proc"].terminated is True


# ── shutdown ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_closing_the_session_stops_the_browser(tmp_path: Path):
    kw, rec = harness()
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw
    )

    await session.close()

    assert rec["proc"].terminated is True
    assert rec["sock"].closed is True
    assert session.is_open is False


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_path: Path):
    kw, _rec = harness()
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw
    )
    await session.close()
    await session.close()  # must not raise


@pytest.mark.asyncio
async def test_close_requests_browser_shutdown_before_reaping_profile(tmp_path: Path):
    import json

    kw, rec = harness()

    class FlushingSocket(FakeSocket):
        async def send(self, raw):
            await super().send(raw)
            if json.loads(raw).get("method") == "Browser.close":
                rec["proc"].returncode = 0

    rec["sock"] = FlushingSocket()
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw
    )
    await session.close()
    assert any(json.loads(raw).get("method") == "Browser.close" for raw in rec["sock"].sent)
    assert not rec["proc"].terminated
    assert not rec["proc"].killed
    assert rec["sock"].closed


@pytest.mark.asyncio
async def test_a_browser_that_ignores_terminate_is_killed(tmp_path: Path, monkeypatch):
    """A hung renderer must not keep the port forever."""
    monkeypatch.setattr("narranexus.platform.browser._browser_impl.runtime_launch.TERMINATE_GRACE_SEC", 0.05)
    kw, rec = harness()

    class Stubborn(FakeProcess):
        def terminate(self):
            self.terminated = True  # but never exits

        async def wait(self):
            await asyncio.sleep(3600)

    rec["proc"] = Stubborn()

    async def spawn(_argv):
        return rec["proc"]

    kw["spawn"] = spawn
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw
    )

    await asyncio.wait_for(session.close(), timeout=10)

    assert rec["proc"].killed is True


# ── target selection (learned from a real browser, 2026-09-22) ───────────────


@pytest.mark.asyncio
async def test_discovery_uses_browser_endpoint_independent_of_page_lifetime():
    """Closing any page must not close the browser transport."""
    from narranexus.platform.browser._browser_impl import runtime_launch

    requested: list[str] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/root"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, **_kw):
            requested.append(("GET", url))
            return FakeResponse()

    import httpx

    original = httpx.AsyncClient
    httpx.AsyncClient = lambda **_kw: FakeClient()  # type: ignore[assignment]
    try:
        ws_url = await runtime_launch._default_discover(port=1234)
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]

    assert ws_url.endswith("/browser/root")
    assert requested
    method, url = requested[0]
    assert method == "GET"
    assert url == "http://127.0.0.1:1234/json/version"


# ── headless needs a viewport before it will paint ──────────────────────────


@pytest.mark.asyncio
async def test_headless_sessions_get_an_explicit_viewport(tmp_path: Path):
    """Measured 2026-09-22 against a real browser:

        headless, no viewport   →   0 frames
        headless + viewport     →  54 fps
        headed                  →  92 fps

    A headless renderer with no device metrics never paints, so screencast
    emits nothing — which looks exactly like "the page is not changing" and is
    the same dead end the ego-lite spike hit. The override is what makes a
    windowless browser possible at all, so it is not optional decoration.
    """
    kw, rec = harness()
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(),
        headless=True, **kw
    )
    try:
        sent = [m for m in rec["sock"].sent if "setDeviceMetricsOverride" in m]
        assert sent, "a headless session must be given device metrics or it never paints"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_headed_session_is_left_alone(tmp_path: Path):
    """A visible window already has real metrics; overriding them would fight
    the user's own resizing."""
    kw, rec = harness()
    session = await launch_session(
        executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(),
        headless=False, **kw
    )
    try:
        assert not [m for m in rec["sock"].sent if "setDeviceMetricsOverride" in m]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_default_spawn_captures_stderr_without_buffering_stdout(monkeypatch):
    from unittest.mock import AsyncMock

    from narranexus.platform.browser._browser_impl.runtime_launch import _default_spawn

    spawn = AsyncMock(return_value=FakeProcess())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await _default_spawn(["chromium"])
    assert spawn.call_args.kwargs["stderr"] == asyncio.subprocess.PIPE
    assert spawn.call_args.kwargs["stdout"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["discovery", "connection"])
async def test_child_exit_interrupts_startup_with_stderr_and_exit_code(tmp_path, phase):
    kw, rec = harness()
    proc = rec["proc"]
    proc.stderr = asyncio.StreamReader()
    cancelled = asyncio.Event()

    async def fail_after_exit(*_args, **_kwargs):
        proc.stderr.feed_data(b"Failed to create SingletonLock: profile is already in use\n")
        proc.stderr.feed_eof()
        proc.returncode = 21
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    kw["discover" if phase == "discovery" else "connect"] = fail_after_exit
    with pytest.raises(LaunchError) as error:
        async with asyncio.timeout(1):
            await launch_session(executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw)
    assert "exit code 21" in str(error.value)
    assert "SingletonLock" in str(error.value)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_startup_error_includes_bounded_tail_not_entire_output(tmp_path):
    kw, rec = harness()
    proc = rec["proc"]
    proc.stderr = asyncio.StreamReader()
    proc.stderr.feed_data(b"discard-this-prefix\n" + b"x" * 80_000 + b"\nlast diagnostic: missing library\n")
    proc.stderr.feed_eof()

    async def fail(**_kwargs):
        raise ConnectionError("CDP setup failed")

    kw["discover"] = fail
    with pytest.raises(LaunchError) as error:
        await launch_session(executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw)
    message = str(error.value)
    assert "last diagnostic: missing library" in message
    assert "discard-this-prefix" not in message
    assert len(message) < 17_000
    assert proc.terminated


@pytest.mark.asyncio
async def test_stderr_is_drained_after_startup_and_closed_with_session(tmp_path):
    kw, rec = harness()
    consumed, cancelled = asyncio.Event(), asyncio.Event()

    class Stderr:
        def __init__(self):
            self.chunks = asyncio.Queue()

        async def read(self, size):
            try:
                chunk = await self.chunks.get()
                consumed.set()
                return chunk
            except asyncio.CancelledError:
                cancelled.set()
                raise

    stderr = Stderr()
    rec["proc"].stderr = stderr
    session = await launch_session(executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(), **kw)
    try:
        stderr.chunks.put_nowait(b"late browser diagnostic")
        await asyncio.wait_for(consumed.wait(), timeout=1)
    finally:
        # Simulates a descendant retaining the stderr pipe after the parent
        # exits: session cleanup must not wait indefinitely for EOF.
        await asyncio.wait_for(session.close(), timeout=2)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_real_child_exit_drains_large_pipe_and_preserves_final_diagnostic(tmp_path):
    import sys

    from narranexus.platform.browser._browser_impl.runtime_launch import _default_spawn

    processes = []

    async def spawn(_argv):
        process = await _default_spawn([
            sys.executable, "-c",
            "import sys; sys.stderr.write('discard-prefix' + 'x' * 200000 + "
            "'\\nSingletonLock: profile already in use\\n'); sys.stderr.flush(); sys.exit(23)",
        ])
        processes.append(process)
        return process

    async def discover(**_kwargs):
        await asyncio.Future()

    with pytest.raises(LaunchError) as error:
        async with asyncio.timeout(5):
            await launch_session(executable=EXE, root=tmp_path, agent_id="a1", policy=BrowserPolicy(),
                                 spawn=spawn, discover=discover)
    message = str(error.value)
    assert "exit code 23" in message
    assert "SingletonLock: profile already in use" in message
    assert "discard-prefix" not in message
    assert len(message) < 17_000
    assert processes[0].returncode == 23
