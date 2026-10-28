"""
@file_name: test_module_runner_single_loop.py
@author: Bin Liang
@date: 2026-04-22
@description: Regression tests for the MCP host (PLAN-2026-04-22-mcp-single-loop.md + plugin platform batch 5a).

Asserts the single-loop, single-port invariants:
1. run_mcp_servers_async serves EVERY module through ONE uvicorn.Server on
   the caller loop (no threading.Thread, no nested anyio.run, no per-module
   process/port): each module's dual-transport app (FastMCP ``sse_app()``
   routes for Claude Code + ``streamable_http_app()`` routes for Codex CLI)
   is mounted at ``/mcp/<server_name>``.
2. The host carries the caller-identity middleware once, a ``/mcp/healthz``
   route, enters every mounted app's lifespan, and disables DNS-rebinding
   protection so other containers reach it by Docker service name.
3. One SIGTERM stops the host (and thereby every module) and releases the port.

These tests are a load-bearing guard: if someone re-introduces threads,
nested event loops or per-module ports, the aiomysql cross-loop bug or the
orphaned-port bug come back.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from narranexus.platform.module_system.module_runner import ModuleRunner


async def _dummy_endpoint(request):  # pragma: no cover — never actually hit
    return PlainTextResponse("ok")


class _FakeMCPServer:
    """Minimal stand-in for a FastMCP server that records how it was run."""

    def __init__(self) -> None:
        self.settings = MagicMock()
        self.sse_app_called = False
        self.streamable_app_called = False
        self.run_called = False  # run("sse") — must never be used
        self.lifespan_entered = 0
        self.last_streamable_app: Starlette | None = None

    def sse_app(self) -> Starlette:
        self.sse_app_called = True
        return Starlette(routes=[Route("/sse", _dummy_endpoint), Route("/messages", _dummy_endpoint)])

    def streamable_http_app(self) -> Starlette:
        import contextlib

        self.streamable_app_called = True

        @contextlib.asynccontextmanager
        async def _lifespan(_app):
            self.lifespan_entered += 1
            yield

        self.last_streamable_app = Starlette(routes=[Route("/mcp", _dummy_endpoint)], lifespan=_lifespan)
        return self.last_streamable_app

    def run(self, transport: str) -> None:
        self.run_called = True
        raise AssertionError("ModuleRunner must serve via uvicorn on the caller loop, not run() (nested anyio.run)")


class _FakeUvicornServer:
    """Captures uvicorn.Config and blocks in serve() until released or should_exit."""

    instances: list["_FakeUvicornServer"] = []
    release: asyncio.Event  # set per-test

    def __init__(self, config) -> None:
        self.config = config
        self.should_exit = False
        _FakeUvicornServer.instances.append(self)

    async def serve(self, sockets=None) -> None:
        while not self.should_exit and not _FakeUvicornServer.release.is_set():
            await asyncio.sleep(0.005)


class _Cfg:
    def __init__(self, server_name: str) -> None:
        self.server_name = server_name


def _module_class(fake: _FakeMCPServer, server_name: str, seen: list | None = None):
    class _FakeModule:
        def __init__(self, agent_id, user_id, database_client):
            if seen is not None:
                seen.append(database_client)

        def create_mcp_server(self):
            return fake

        # ModuleRunner serves via the base-class wrapper (it installs
        # caller-identity resolution); mirror it on the fake.
        def build_instrumented_mcp_server(self):
            return self.create_mcp_server()

        async def mcp_server(self):
            return _Cfg(server_name)

    _FakeModule.__name__ = f"Fake_{server_name}"
    return _FakeModule


@pytest.fixture
def fake_uvicorn():
    _FakeUvicornServer.instances = []
    _FakeUvicornServer.release = asyncio.Event()
    with patch("uvicorn.Server", _FakeUvicornServer):
        yield _FakeUvicornServer


def _stub_db(monkeypatch):
    """Stub get_db_client + auto_migrate so run_mcp_servers_async does no IO."""

    async def _fake_get_db_client():
        m = MagicMock()
        m._backend = MagicMock()
        return m

    async def _fake_auto_migrate(_backend):
        return None

    monkeypatch.setattr("narranexus.platform.module_system.module_runner.get_db_client", _fake_get_db_client)
    monkeypatch.setattr("narranexus.platform.utils.db.schema_registry.auto_migrate", _fake_auto_migrate)
    # The real boot freezes the process-wide KERNEL_REGISTRIES; later tests still register.
    monkeypatch.setattr("narranexus.platform.module_system.plugins_boot.boot_mcp_plugins", lambda: None)


def _mounts(app) -> dict[str, Starlette]:
    return {r.path: r.app for r in app.router.routes if isinstance(r, Mount)}


def test_module_app_merges_both_transports_and_keeps_the_streamable_lifespan():
    server = _FakeMCPServer()
    app = ModuleRunner._build_module_app(server)
    assert server.sse_app_called and server.streamable_app_called and server.run_called is False
    assert server.settings.host == "0.0.0.0"
    assert server.settings.transport_security.enable_dns_rebinding_protection is False
    assert {route.path for route in app.router.routes} == {"/sse", "/messages", "/mcp"}
    assert app.router.lifespan_context is server.last_streamable_app.router.lifespan_context


@pytest.mark.asyncio
async def test_host_mounts_every_module_by_path_with_healthz_and_identity_middleware(fake_uvicorn):
    from starlette.testclient import TestClient

    from narranexus.platform.module_system.identity.mcp_auth import IdentityAuthMiddleware

    a, b = _FakeMCPServer(), _FakeMCPServer()
    server = ModuleRunner._build_host_server([("chat_module", a), ("job_module", b)], 19901)
    config = server.config
    assert config.host == "0.0.0.0" and config.port == 19901
    app = config.app
    assert set(_mounts(app)) == {"/mcp/chat_module", "/mcp/job_module"}
    assert IdentityAuthMiddleware in [m.cls for m in app.user_middleware]
    with TestClient(app) as client:  # the host lifespan enters every mounted app's lifespan
        r = client.get("/mcp/healthz")
        assert r.status_code == 200 and r.json()["servers"] == ["chat_module", "job_module"] and r.json()["port"] == 19901
    assert a.lifespan_entered == 1 and b.lifespan_entered == 1


@pytest.mark.asyncio
async def test_run_mcp_servers_async_serves_one_host_on_the_caller_loop(monkeypatch, fake_uvicorn):
    """ONE uvicorn server, every module mounted, no threads, the configured port."""
    runner = ModuleRunner()
    fake_a, fake_b = _FakeMCPServer(), _FakeMCPServer()
    modules = [_module_class(fake_a, "alpha_module"), _module_class(fake_b, "beta_module")]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)
    monkeypatch.setenv("MCP_PORT", "19911")
    _stub_db(monkeypatch)

    import threading as _threading

    original_thread = _threading.Thread
    thread_spawn_count = {"n": 0}

    class _Tripwire(original_thread):
        def __init__(self, *a, **kw):
            thread_spawn_count["n"] += 1
            super().__init__(*a, **kw)

    monkeypatch.setattr(_threading, "Thread", _Tripwire)

    async def _stopper():
        await asyncio.sleep(0.1)
        fake_uvicorn.release.set()

    stopper_task = asyncio.create_task(_stopper())
    try:
        await asyncio.wait_for(runner.run_mcp_servers_async(agent_id="test_agent", user_id="test_user", modules=modules), timeout=5.0)
    finally:
        await stopper_task

    assert fake_a.sse_app_called and fake_a.streamable_app_called and fake_b.sse_app_called and fake_b.streamable_app_called
    assert fake_a.run_called is False and fake_b.run_called is False
    assert len(fake_uvicorn.instances) == 1, "one uvicorn server hosts every module"
    config = fake_uvicorn.instances[0].config
    assert config.port == 19911
    assert set(_mounts(config.app)) == {"/mcp/alpha_module", "/mcp/beta_module"}
    assert thread_spawn_count["n"] == 0, "run_mcp_servers_async must not spawn any threads"


@pytest.mark.asyncio
async def test_run_mcp_servers_async_boots_plugins_before_resolving_modules(monkeypatch, fake_uvicorn):
    """The module table is registry-derived and the registry is filled by the mcp
    boot: a runner that resolves modules first sees an empty table and exits with
    "No modules to run" (the whole MCP host dead). The boot must come first."""
    runner = ModuleRunner()
    fake = _FakeMCPServer()
    module_cls = _module_class(fake, "booted_module")
    _stub_db(monkeypatch)
    monkeypatch.setenv("MCP_PORT", "19913")
    booted = {"done": False}

    def _boot():
        booted["done"] = True

    def _resolve(_modules):
        # What the real registry-derived resolution does: nothing before boot.
        return [module_cls] if booted["done"] else []

    monkeypatch.setattr("narranexus.platform.module_system.plugins_boot.boot_mcp_plugins", _boot)
    monkeypatch.setattr(runner, "_resolve_modules", _resolve)

    async def _stopper():
        await asyncio.sleep(0.1)
        fake_uvicorn.release.set()

    stopper_task = asyncio.create_task(_stopper())
    try:
        await asyncio.wait_for(runner.run_mcp_servers_async(agent_id="test_agent", user_id="test_user"), timeout=5.0)
    finally:
        await stopper_task

    assert booted["done"]
    assert len(fake_uvicorn.instances) == 1, "the host must start: modules resolve only after boot"
    assert set(_mounts(fake_uvicorn.instances[0].config.app)) == {"/mcp/booted_module"}


@pytest.mark.asyncio
async def test_async_runner_is_credfree_when_seam_is_httpstore(monkeypatch, fake_uvicorn):
    """When NARRANEXUS_BACKEND_URL is set (seam=HttpStore, the creds-stripped
    cloud shape), run_mcp_servers_async must NOT open a DB pool or run
    auto_migrate, and must construct modules with database_client=None."""
    runner = ModuleRunner()
    seen = {"db_client_calls": 0, "auto_migrate_calls": 0}
    clients: list = []
    modules = [_module_class(_FakeMCPServer(), "solo_module", clients)]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)

    async def _boom_db():
        seen["db_client_calls"] += 1
        raise AssertionError("get_db_client must not run in seam/HttpStore mode")

    async def _boom_migrate(_backend):
        seen["auto_migrate_calls"] += 1
        raise AssertionError("auto_migrate must not run in seam/HttpStore mode")

    monkeypatch.setattr("narranexus.platform.module_system.module_runner.get_db_client", _boom_db)
    monkeypatch.setattr("narranexus.platform.utils.db.schema_registry.auto_migrate", _boom_migrate)
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    monkeypatch.setattr("narranexus.platform.module_system.plugins_boot.boot_mcp_plugins", lambda: None)

    async def _stopper():
        await asyncio.sleep(0.1)
        fake_uvicorn.release.set()

    stopper_task = asyncio.create_task(_stopper())
    try:
        await asyncio.wait_for(runner.run_mcp_servers_async(agent_id="a", user_id="u", modules=modules), timeout=5.0)
    finally:
        await stopper_task

    assert seen == {"db_client_calls": 0, "auto_migrate_calls": 0}
    assert clients == [None], "modules must get database_client=None"


@pytest.mark.asyncio
async def test_sigterm_stops_the_host(monkeypatch, fake_uvicorn):
    """One SIGTERM must stop the host — the only server — so the process
    unwinds and the port is released (the orphaned-sidecar regression guard)."""
    runner = ModuleRunner()
    modules = [_module_class(_FakeMCPServer(), f"m{i}") for i in range(3)]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)
    _stub_db(monkeypatch)

    loop = asyncio.get_running_loop()
    registered: dict[int, object] = {}
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, cb, *a: registered.__setitem__(sig, cb))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: True)

    async def _fire_sigterm_once_ready():
        for _ in range(500):
            if signal.SIGTERM in registered and len(fake_uvicorn.instances) == 1:
                break
            await asyncio.sleep(0.005)
        registered[signal.SIGTERM]()  # simulate the SIGTERM delivery

    fire_task = asyncio.create_task(_fire_sigterm_once_ready())
    # NOTE: fake_uvicorn.release is never set — the ONLY way serve() can
    # complete is the handler flipping should_exit on the host.
    await asyncio.wait_for(runner.run_mcp_servers_async(modules=modules), timeout=5.0)
    await fire_task

    assert signal.SIGINT in registered and signal.SIGTERM in registered
    assert fake_uvicorn.instances[0].should_exit is True


def test_build_host_server_neutralises_uvicorn_signal_capture():
    """The host's own signal capture must be a no-op so it cannot clobber the
    centralised handler. Uses a REAL uvicorn.Server."""
    server = ModuleRunner._build_host_server([("fake_module", _FakeMCPServer())], 19931)
    sentinel = lambda *_a: None  # noqa: E731
    previous = signal.signal(signal.SIGTERM, sentinel)
    try:
        with server.capture_signals():
            assert signal.getsignal(signal.SIGTERM) is sentinel
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_module_urls_point_at_the_single_host(monkeypatch):
    """The agent side dials one base URL: MCP_HOST/MCP_PORT (or MCP_BASE_URL) + the mount path."""
    from narranexus.platform.module_system.base import mcp_base_url, mcp_mount_path, mcp_server_url

    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.setenv("MCP_HOST", "mcp")
    monkeypatch.setenv("MCP_PORT", "7801")
    assert mcp_base_url() == "http://mcp:7801" and mcp_mount_path("chat_module") == "/mcp/chat_module"
    assert mcp_server_url("chat_module") == "http://mcp:7801/mcp/chat_module/sse"
    monkeypatch.setenv("MCP_BASE_URL", "https://edge.example/mcp-host/")
    assert mcp_server_url("job_module") == "https://edge.example/mcp-host/mcp/job_module/sse"


@pytest.mark.asyncio
async def test_sigterm_closes_the_db_pool_it_opened(monkeypatch, fake_uvicorn):
    """B-41: in local/direct-MySQL mode this process is the one that opened
    the pool (`get_db_client()`, bound to THIS loop — aiomysql binds its
    Futures to the creating loop). Without closing it before returning,
    `asyncio.run()` tears the loop down with the pool still open; aiomysql's
    connections are then finalized by GC after the loop is already closed,
    logging "Event loop is closed" once per leaked connection (dev logs).
    """
    runner = ModuleRunner()
    modules = [_module_class(_FakeMCPServer(), "solo_module")]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)
    _stub_db(monkeypatch)

    close_calls = {"n": 0}

    async def _fake_close_db_client():
        close_calls["n"] += 1

    monkeypatch.setattr(
        "narranexus.platform.module_system.module_runner.close_db_client", _fake_close_db_client
    )

    loop = asyncio.get_running_loop()
    registered: dict[int, object] = {}
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, cb, *a: registered.__setitem__(sig, cb))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: True)

    async def _fire_sigterm_once_ready():
        for _ in range(500):
            if signal.SIGTERM in registered and len(fake_uvicorn.instances) == 1:
                break
            await asyncio.sleep(0.005)
        registered[signal.SIGTERM]()

    fire_task = asyncio.create_task(_fire_sigterm_once_ready())
    await asyncio.wait_for(runner.run_mcp_servers_async(modules=modules), timeout=5.0)
    await fire_task

    assert close_calls["n"] == 1, "the pool this process opened must be closed on the SAME loop before shutdown returns"


@pytest.mark.asyncio
async def test_sigterm_does_not_close_a_pool_it_never_opened(monkeypatch, fake_uvicorn):
    """The seam/HttpStore (creds-free) mode never opens a pool
    (`test_async_runner_is_credfree_when_seam_is_httpstore`) — shutdown must
    not call close_db_client() in that mode either, since doing so would
    reach for a per-loop client this process never created."""
    runner = ModuleRunner()
    modules = [_module_class(_FakeMCPServer(), "solo_module")]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)

    async def _boom_db():
        raise AssertionError("get_db_client must not run in seam/HttpStore mode")

    async def _boom_migrate(_backend):
        raise AssertionError("auto_migrate must not run in seam/HttpStore mode")

    monkeypatch.setattr("narranexus.platform.module_system.module_runner.get_db_client", _boom_db)
    monkeypatch.setattr("narranexus.platform.utils.db.schema_registry.auto_migrate", _boom_migrate)
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    monkeypatch.setattr("narranexus.platform.module_system.plugins_boot.boot_mcp_plugins", lambda: None)

    close_calls = {"n": 0}

    async def _fake_close_db_client():
        close_calls["n"] += 1

    monkeypatch.setattr(
        "narranexus.platform.module_system.module_runner.close_db_client", _fake_close_db_client
    )

    loop = asyncio.get_running_loop()
    registered: dict[int, object] = {}
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, cb, *a: registered.__setitem__(sig, cb))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: True)

    async def _fire_sigterm_once_ready():
        for _ in range(500):
            if signal.SIGTERM in registered and len(fake_uvicorn.instances) == 1:
                break
            await asyncio.sleep(0.005)
        registered[signal.SIGTERM]()

    fire_task = asyncio.create_task(_fire_sigterm_once_ready())
    await asyncio.wait_for(runner.run_mcp_servers_async(modules=modules), timeout=5.0)
    await fire_task

    assert close_calls["n"] == 0


# ── every exit after the pool opens releases it (review I5), and detached ──
# ── work is joined BEFORE the pool closes (review M5) ─────────────────────


def _record_close(monkeypatch, log: list):
    async def _fake_close_db_client():
        log.append("closed")

    monkeypatch.setattr(
        "narranexus.platform.module_system.module_runner.close_db_client", _fake_close_db_client
    )


@pytest.mark.asyncio
async def test_the_no_servers_early_return_still_closes_the_pool(monkeypatch, fake_uvicorn):
    """The first version closed the pool only in serve()'s finally; the
    `if not instances: return` exit sits BEFORE that block and left the pool
    open — the start-up-failure log (the one you most need to read) still got
    the 11 "Event loop is closed" lines."""
    runner = ModuleRunner()

    class _NoServerModule:
        def __init__(self, agent_id, user_id, database_client):
            pass

        def build_instrumented_mcp_server(self):
            return None

        async def mcp_server(self):
            return None

    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: [_NoServerModule])
    _stub_db(monkeypatch)
    log: list = []
    _record_close(monkeypatch, log)

    await asyncio.wait_for(runner.run_mcp_servers_async(modules=[_NoServerModule]), timeout=5.0)

    assert log == ["closed"]
    assert fake_uvicorn.instances == [], "nothing to serve — no host must start"


@pytest.mark.asyncio
async def test_a_failing_auto_migrate_still_closes_the_pool_and_propagates(monkeypatch, fake_uvicorn):
    runner = ModuleRunner()
    modules = [_module_class(_FakeMCPServer(), "solo_module")]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)
    _stub_db(monkeypatch)

    async def _boom_migrate(_backend):
        raise RuntimeError("DDL grant missing")

    monkeypatch.setattr("narranexus.platform.utils.db.schema_registry.auto_migrate", _boom_migrate)
    log: list = []
    _record_close(monkeypatch, log)

    with pytest.raises(RuntimeError, match="DDL grant missing"):
        await runner.run_mcp_servers_async(modules=modules)

    assert log == ["closed"]


@pytest.mark.asyncio
async def test_detached_work_is_joined_before_the_pool_closes(monkeypatch, fake_uvicorn):
    """`spawn`ed work (a hook starting a run, a dataloader flush) that is still
    in flight when the pool closes hits `_backend=None`. The sibling
    entrypoints stop their workers FIRST and close the pool LAST; this host
    must keep the same order."""
    from narranexus.platform.utils import spawn

    runner = ModuleRunner()
    modules = [_module_class(_FakeMCPServer(), "solo_module")]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)
    _stub_db(monkeypatch)
    log: list = []
    _record_close(monkeypatch, log)

    async def _detached_work():
        await asyncio.sleep(0.05)
        log.append("task_done")

    async def _spawn_then_stop():
        for _ in range(500):
            if len(fake_uvicorn.instances) == 1:
                break
            await asyncio.sleep(0.005)
        spawn(_detached_work(), name="test-detached-work")
        fake_uvicorn.release.set()

    stopper = asyncio.create_task(_spawn_then_stop())
    await asyncio.wait_for(runner.run_mcp_servers_async(modules=modules), timeout=5.0)
    await stopper

    assert log == ["task_done", "closed"]


@pytest.mark.asyncio
async def test_a_wedged_detached_task_still_leaves_time_to_close_the_pool(monkeypatch, fake_uvicorn):
    """The drain is bounded BELOW the tightest stop grace (Tauri: 3s SIGTERM ->
    SIGKILL; Docker default: 10s). A drain that uses the whole grace gets the
    process SIGKILLed before `close_db_client()` -- the in-flight-work case is
    exactly the one this shutdown order exists for. A task that never finishes
    must be cancelled and the pool closed, all inside the grace."""
    import time

    from narranexus.platform.module_system import module_runner as mr
    from narranexus.platform.utils import spawn

    runner = ModuleRunner()
    modules = [_module_class(_FakeMCPServer(), "solo_module")]
    monkeypatch.setattr(runner, "_resolve_modules", lambda _m: modules)
    _stub_db(monkeypatch)
    log: list = []
    _record_close(monkeypatch, log)

    async def _wedged_work():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            log.append("task_cancelled")
            raise

    async def _spawn_then_stop():
        for _ in range(500):
            if len(fake_uvicorn.instances) == 1:
                break
            await asyncio.sleep(0.005)
        spawn(_wedged_work(), name="test-wedged-work")
        fake_uvicorn.release.set()

    stopper = asyncio.create_task(_spawn_then_stop())
    started = time.monotonic()
    await asyncio.wait_for(runner.run_mcp_servers_async(modules=modules), timeout=15.0)
    elapsed = time.monotonic() - started
    await stopper

    assert log == ["task_cancelled", "closed"]
    assert mr._BACKGROUND_DRAIN_SEC + mr._POOL_CLOSE_HEADROOM_SEC <= mr._STOP_GRACE_BUDGET_SEC
    assert elapsed < mr._STOP_GRACE_BUDGET_SEC
