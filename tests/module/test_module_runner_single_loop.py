"""
@file_name: test_module_runner_single_loop.py
@author: Bin Liang
@date: 2026-04-22
@description: Regression tests for PLAN-2026-04-22-mcp-single-loop.md.

Asserts the single-loop MCP invariants:
1. run_mcp_servers_async launches every MCP server via asyncio.gather on
   the caller loop (no threading.Thread, no nested anyio.run).
2. Each server is served through ONE uvicorn.Server whose app merges the
   routes of FastMCP's ``sse_app()`` (/sse + /messages, Claude Code) and
   ``streamable_http_app()`` (/mcp, Codex CLI) at the root level — the
   dual-transport shape. The sync ``run("sse")`` entry point must never
   be used: it would spawn a new loop inside the caller via anyio.run.
3. _serve_one_mcp configures DNS-rebinding transport security so other
   containers can reach the server by Docker service name.

These tests are a load-bearing guard: if someone re-introduces threads
or nested event loops, the aiomysql cross-loop bug will come back.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from xyz_agent_context.module.module_runner import ModuleRunner


async def _dummy_endpoint(request):  # pragma: no cover — never actually hit
    return PlainTextResponse("ok")


class _FakeMCPServer:
    """Minimal stand-in for a FastMCP server that records how it was run.

    Provides only the surface `_serve_one_mcp` touches: a `settings`
    namespace plus the two transport-app factories whose routes get
    merged into the single uvicorn app.
    """

    def __init__(self) -> None:
        self.settings = MagicMock()
        self.sse_app_called = False
        self.streamable_app_called = False
        self.run_called = False  # run("sse") — must never be used
        self.last_streamable_app: Starlette | None = None

    def sse_app(self) -> Starlette:
        self.sse_app_called = True
        return Starlette(
            routes=[
                Route("/sse", _dummy_endpoint),
                Route("/messages", _dummy_endpoint),
            ]
        )

    def streamable_http_app(self) -> Starlette:
        self.streamable_app_called = True
        self.last_streamable_app = Starlette(
            routes=[Route("/mcp", _dummy_endpoint)],
        )
        return self.last_streamable_app

    def run(self, transport: str) -> None:
        # If production code ever regresses back to the sync entry point
        # we want the test to scream — sync run() is what calls anyio.run
        # and re-introduces the multi-loop shape.
        self.run_called = True
        raise AssertionError(
            "ModuleRunner must serve via uvicorn on the caller loop, not "
            "run(). Calling run() would create a nested event loop via "
            "anyio.run."
        )


class _FakeUvicornServer:
    """Captures uvicorn.Config and blocks in serve() until released,
    mimicking a real server's behaviour without binding a port.

    ``serve()`` returns when EITHER the shared ``release`` event fires OR
    this server's ``should_exit`` flag is set — the latter mirrors how a
    real uvicorn.Server exits once its ``should_exit`` is flipped, which is
    exactly what the centralised SIGTERM handler does to every server.
    """

    instances: list["_FakeUvicornServer"] = []
    release: asyncio.Event  # set per-test

    def __init__(self, config) -> None:
        self.config = config
        self.should_exit = False
        _FakeUvicornServer.instances.append(self)

    async def serve(self, sockets=None) -> None:
        while not self.should_exit and not _FakeUvicornServer.release.is_set():
            await asyncio.sleep(0.005)


@pytest.fixture
def fake_uvicorn():
    _FakeUvicornServer.instances = []
    _FakeUvicornServer.release = asyncio.Event()
    with patch("uvicorn.Server", _FakeUvicornServer):
        yield _FakeUvicornServer


@pytest.mark.asyncio
async def test_serve_one_mcp_uses_run_sse_async_and_configures_transport(fake_uvicorn):
    """_serve_one_mcp must merge both transport apps into one uvicorn
    server on the caller loop and disable DNS rebinding protection so
    Docker service names work. (Test name kept stable — 'single loop,
    no sync run()' is still the invariant under guard.)"""
    server = _FakeMCPServer()

    task = asyncio.create_task(ModuleRunner._serve_one_mcp(server, "FakeModule", 19901))
    # Give the task a tick to reach serve().
    await asyncio.sleep(0.05)

    try:
        assert server.sse_app_called, "sse_app() routes must be mounted (Claude Code)"
        assert server.streamable_app_called, "streamable_http_app() routes must be mounted (Codex CLI)"
        assert server.run_called is False, "sync run() must not be used"
        assert server.settings.host == "0.0.0.0"
        assert server.settings.port == 19901
        # transport_security was assigned with rebinding disabled.
        assert server.settings.transport_security is not None
        assert server.settings.transport_security.enable_dns_rebinding_protection is False

        # One uvicorn server, root-level merged routes, right bind params.
        assert len(fake_uvicorn.instances) == 1
        config = fake_uvicorn.instances[0].config
        assert config.host == "0.0.0.0"
        assert config.port == 19901
        wrapped = config.app
        paths = {route.path for route in wrapped.router.routes}
        assert {"/sse", "/messages", "/mcp"} <= paths, f"merged app must serve all transport paths at root, got {paths}"
        # The streamable app owns the StreamableHTTPSessionManager via its
        # lifespan; the merged app must adopt it or /mcp sessions never start.
        assert wrapped.router.lifespan_context is (server.last_streamable_app.router.lifespan_context)
    finally:
        fake_uvicorn.release.set()
        await task


@pytest.mark.asyncio
async def test_run_mcp_servers_async_uses_gather_not_threads(monkeypatch, fake_uvicorn):
    """run_mcp_servers_async must launch servers concurrently on the
    current loop, NOT spawn threading.Threads. If anything ever imports
    and uses threading inside this method, this test fails."""
    runner = ModuleRunner()

    fake_a = _FakeMCPServer()
    fake_b = _FakeMCPServer()

    # Dummy module classes with real __init__ signatures so the runner
    # can construct them with (agent_id=..., user_id=..., database_client=...).
    class _FakeModuleA:
        def __init__(self, agent_id, user_id, database_client):
            pass

        def create_mcp_server(self):
            return fake_a

    class _FakeModuleB:
        def __init__(self, agent_id, user_id, database_client):
            pass

        def create_mcp_server(self):
            return fake_b

    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: [_FakeModuleA, _FakeModuleB])

    # Override the port lookup so server names align with our fakes.
    monkeypatch.setattr(
        "xyz_agent_context.module.module_runner.MODULE_PORTS",
        {"_FakeModuleA": 19911, "_FakeModuleB": 19912},
    )

    # Tripwire: any threading.Thread construction during the async path
    # signals a regression back to the multi-loop architecture.
    import threading as _threading

    original_thread = _threading.Thread
    thread_spawn_count = {"n": 0}

    class _Tripwire(original_thread):
        def __init__(self, *a, **kw):
            thread_spawn_count["n"] += 1
            super().__init__(*a, **kw)

    monkeypatch.setattr(_threading, "Thread", _Tripwire)

    # Stub get_db_client + auto_migrate to avoid real DB IO.
    async def _fake_get_db_client():
        m = MagicMock()
        m._backend = MagicMock()
        return m

    async def _fake_auto_migrate(_backend):
        return None

    monkeypatch.setattr(
        "xyz_agent_context.module.module_runner.get_db_client",
        _fake_get_db_client,
    )
    monkeypatch.setattr(
        "xyz_agent_context.utils.db.schema_registry.auto_migrate",
        _fake_auto_migrate,
    )

    # Release both fake uvicorn servers shortly after launch so gather
    # completes.
    async def _stopper():
        await asyncio.sleep(0.1)
        fake_uvicorn.release.set()

    stopper_task = asyncio.create_task(_stopper())
    try:
        await asyncio.wait_for(
            runner.run_mcp_servers_async(
                agent_id="test_agent",
                user_id="test_user",
                modules=[_FakeModuleA, _FakeModuleB],
            ),
            timeout=5.0,
        )
    finally:
        await stopper_task

    assert fake_a.sse_app_called and fake_a.streamable_app_called, (
        "server A must be served through the merged dual-transport app"
    )
    assert fake_b.sse_app_called and fake_b.streamable_app_called, (
        "server B must be served through the merged dual-transport app"
    )
    assert fake_a.run_called is False and fake_b.run_called is False
    assert len(fake_uvicorn.instances) == 2, "one uvicorn server per module"
    assert thread_spawn_count["n"] == 0, (
        "run_mcp_servers_async must not spawn any threads — that would "
        "recreate the multi-loop architecture. "
        f"Observed {thread_spawn_count['n']} Thread() constructions."
    )


def _stub_db(monkeypatch):
    """Stub get_db_client + auto_migrate so run_mcp_servers_async does no IO."""

    async def _fake_get_db_client():
        m = MagicMock()
        m._backend = MagicMock()
        return m

    async def _fake_auto_migrate(_backend):
        return None

    monkeypatch.setattr("xyz_agent_context.module.module_runner.get_db_client", _fake_get_db_client)
    monkeypatch.setattr("xyz_agent_context.utils.db.schema_registry.auto_migrate", _fake_auto_migrate)


@pytest.mark.asyncio
async def test_sigterm_stops_every_server_not_just_the_last(monkeypatch, fake_uvicorn):
    """A single SIGTERM must gracefully stop ALL MCP servers, not only the
    last one that happened to register uvicorn's own signal handler.

    Regression guard for the orphaned-sidecar bug: uvicorn's per-server
    ``capture_signals`` installs a process-global ``signal.signal`` handler,
    so with N servers on one loop the last one wins and a SIGTERM stops only
    that server — the other N-1 keep serving and hold their ports, hanging
    process exit until an external SIGKILL. run_mcp_servers_async must instead
    install ONE handler that flips ``should_exit`` on every server so the
    whole process unwinds and releases every port.
    """
    runner = ModuleRunner()

    # Three servers so "only the last one exits" is clearly distinguishable
    # from "all exit".
    fakes = [_FakeMCPServer() for _ in range(3)]

    class _M0:
        def __init__(self, agent_id, user_id, database_client):
            pass

        def create_mcp_server(self):
            return fakes[0]

    class _M1:
        def __init__(self, agent_id, user_id, database_client):
            pass

        def create_mcp_server(self):
            return fakes[1]

    class _M2:
        def __init__(self, agent_id, user_id, database_client):
            pass

        def create_mcp_server(self):
            return fakes[2]

    modules = [_M0, _M1, _M2]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)
    monkeypatch.setattr(
        "xyz_agent_context.module.module_runner.MODULE_PORTS",
        {"_M0": 19921, "_M1": 19922, "_M2": 19923},
    )
    _stub_db(monkeypatch)

    # Capture the signal handler run_mcp_servers_async installs instead of
    # letting it touch the real test-process signal disposition.
    loop = asyncio.get_running_loop()
    registered: dict[int, object] = {}
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda sig, cb, *a: registered.__setitem__(sig, cb),
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: True)

    async def _fire_sigterm_once_ready():
        # Wait until all three servers are built and the handler is installed.
        for _ in range(500):
            if signal.SIGTERM in registered and len(fake_uvicorn.instances) == 3:
                break
            await asyncio.sleep(0.005)
        registered[signal.SIGTERM]()  # simulate the SIGTERM delivery

    fire_task = asyncio.create_task(_fire_sigterm_once_ready())
    # NOTE: fake_uvicorn.release is never set here — the ONLY way gather can
    # complete is if the handler flips should_exit on every server.
    await asyncio.wait_for(runner.run_mcp_servers_async(modules=modules), timeout=5.0)
    await fire_task

    assert signal.SIGINT in registered and signal.SIGTERM in registered, (
        "run_mcp_servers_async must centrally handle both SIGINT and SIGTERM"
    )
    assert all(s.should_exit for s in fake_uvicorn.instances), (
        "every server must be told to exit on one SIGTERM, not just the last"
    )


def test_build_mcp_server_neutralises_uvicorn_signal_capture():
    """Each built server's own signal capture must be a no-op so it cannot
    clobber the centralised handler (see the test above).

    Uses a REAL uvicorn.Server: a stock one's ``capture_signals`` would swap
    SIGTERM for its ``handle_exit``; the neutralised one must not.
    """
    server = ModuleRunner._build_mcp_server(_FakeMCPServer(), "FakeModule", 19931)
    sentinel = lambda *_a: None  # noqa: E731
    previous = signal.signal(signal.SIGTERM, sentinel)
    try:
        with server.capture_signals():
            # A real uvicorn server would have replaced SIGTERM with its own
            # handle_exit here; the neutralised one must leave ours in place.
            assert signal.getsignal(signal.SIGTERM) is sentinel
    finally:
        signal.signal(signal.SIGTERM, previous)
