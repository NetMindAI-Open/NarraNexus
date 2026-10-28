"""
@file_name: test_executor_service_busy.py
@date: 2026-08-24
@description: /health's ``busy`` flag — the signal the broker's idle reaper
consults before stopping a container.

The broker only ever sees turn START, so its idle timer cannot tell a
multi-hour turn (binding rule #14: first-class) from an abandoned container.
This flag is what tells them apart, which makes two properties load-bearing:

  * it must be TRUE for the whole duration of a streaming turn, and
  * it must come back DOWN on every exit path, including the ones no route
    handler can observe — otherwise the container reports busy forever and
    stops being reapable at all.
"""
from __future__ import annotations

import anyio
import pytest
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from narranexus.platform.agent_runtime import executor_service as es


@pytest.fixture(autouse=True)
def _clear_inflight():
    es._inflight_started.clear()
    yield
    es._inflight_started.clear()


@pytest.mark.asyncio
async def test_health_is_idle_with_nothing_in_flight():
    body = await es.health()
    assert body["status"] == "healthy"
    assert body["busy"] is False
    assert body["inflight_work"] == 0
    assert body["inflight_oldest_s"] is None


@pytest.mark.asyncio
async def test_health_reports_busy_and_the_oldest_age():
    es._inflight_started[1] = es.monotonic() - 42.0
    es._inflight_started[2] = es.monotonic()

    body = await es.health()
    assert body["busy"] is True
    assert body["inflight_work"] == 2
    # The OLDEST, not the newest: a count alone cannot distinguish a
    # legitimate long turn from a request that never ends.
    assert body["inflight_oldest_s"] >= 42.0


# --------------------------------------------------------------------------
# The middleware, exercised through a real ASGI stack
# --------------------------------------------------------------------------


def _app(handler, path="/agent-loop"):
    app = Starlette(routes=[Route(path, handler, methods=["GET", "POST"])])
    return es.InFlightWorkMiddleware(app)


@pytest.mark.asyncio
async def test_busy_is_true_while_a_stream_is_open():
    seen = []

    async def handler(request):
        async def body():
            seen.append((await es.health())["busy"])
            yield b"chunk\n"

        return StreamingResponse(body())

    with TestClient(_app(handler)) as client:
        assert client.post("/agent-loop").status_code == 200

    assert seen == [True]                    # busy DURING the stream
    assert (await es.health())["busy"] is False   # released after


def test_the_slot_is_released_when_the_body_never_runs():
    """The reason this accounting is middleware and not a `finally` in the
    handler: StreamingResponse sends http.response.start BEFORE touching
    body_iterator, so a generator whose consumer is already gone never runs —
    and closing a never-started async generator executes none of its code,
    `finally` included. Bracketing ``await self.app(...)`` is the only layer
    every exit path passes through, so that is what this asserts: whatever
    happens after the response starts, the slot comes back.

    Driven against a raw inner app rather than a StreamingResponse: the point
    is the middleware's bracket, and reproducing a mid-flight disconnect
    through starlette's disconnect watcher makes the test depend on its
    internals (and deadlock on them)."""
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        assert es._inflight_started, "the slot must be held while work is live"
        raise RuntimeError("consumer vanished before the body iterator ran")

    async def drive():
        app = es.InFlightWorkMiddleware(inner)
        scope = {
            "type": "http", "method": "POST", "path": "/agent-loop",
            "headers": [], "query_string": b"",
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            return None

        with pytest.raises(RuntimeError):
            await app(scope, receive, send)

    anyio.run(drive)

    assert es._inflight_started == {}


def test_a_handler_exception_releases_the_slot():
    async def handler(request):
        raise RuntimeError("boom")

    with TestClient(_app(handler), raise_server_exceptions=False) as client:
        client.post("/agent-loop")

    assert es._inflight_started == {}


def test_health_never_marks_the_container_busy():
    """A self-fulfilling probe would pin every container busy forever: the
    broker's own health request would be the in-flight work it reports."""
    async def handler(request):
        return StreamingResponse(iter([b"ok"]))

    app = _app(handler, path="/health")
    with TestClient(app) as client:
        client.get("/health")

    assert es._inflight_started == {}


@pytest.mark.asyncio
async def test_watch_paths_count_as_work():
    """Office-watch runs INSIDE the container; reaping under it destroys that
    session too, so it has to hold the container busy."""
    seen = []

    async def handler(request):
        seen.append((await es.health())["busy"])
        return StreamingResponse(iter([b"ok"]))

    app = _app(handler, path="/watch/{port}/{path:path}")
    with TestClient(app) as client:
        client.get("/watch/9000/index.html")

    assert seen == [True]


@pytest.mark.asyncio
async def test_agent_loop_gives_up_on_a_body_that_never_arrives(monkeypatch):
    """/agent-loop is unauthenticated and reachable from inside this very
    container: the agent's own Bash can POST a chunked request and never close
    it. Unbounded, request.json() waits forever while the middleware holds the
    container busy — i.e. never reapable. A parse budget only; the turn itself
    stays unbounded (rule #14)."""
    monkeypatch.setattr(es, "_BODY_READ_TIMEOUT_S", 0.05)

    class _Wedged:
        async def json(self):
            await anyio.sleep(30)

    resp = await es.agent_loop(_Wedged())      # returns, does not hang
    assert resp.status_code == 408


def test_the_middleware_is_installed_on_the_real_app():
    """A wiring test. Every test above drives InFlightWorkMiddleware directly,
    so deleting `app.add_middleware(...)` leaves them all green — while
    /health starts reporting busy=False for a container that is mid-turn, and
    the broker reaps it. That is the failure this whole change exists to
    prevent, and it would ship silently."""
    assert any(
        m.cls is es.InFlightWorkMiddleware for m in es.app.user_middleware
    ), "InFlightWorkMiddleware is not installed — /health would always say idle"


def test_health_is_reachable_through_the_real_app_stack():
    """And the flag survives the real middleware stack, in the real shape the
    broker probes: GET /health, 200, a ``busy`` key it can read (the lifespan
    boots into private registries — see conftest)."""
    with TestClient(es.app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["busy"] is False          # nothing in flight but this probe
    assert body["status"] == "healthy"


@pytest.mark.asyncio
async def test_the_watch_passthrough_bounds_its_read_gap(monkeypatch):
    """Wiring: drop the sock_read argument and every other test here stays
    green, while a half-closed client (laptop sleeps, NAT drops the mapping —
    no FIN, so no http.disconnect ever arrives) pins the container busy for
    the rest of its life."""
    import aiohttp

    seen = {}

    class _Session:
        def __init__(self, *a, **kw):
            seen["timeout"] = kw.get("timeout")

        async def get(self, url, headers=None):
            raise aiohttp.ClientError("no upstream in this test")

        async def close(self):
            return None

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)

    class _Req:
        url = type("U", (), {"query": ""})()
        headers: dict = {}

    from narranexus.platform.utils.office_watch import WATCH_PORT_MIN

    resp = await es.watch_passthrough(WATCH_PORT_MIN, "events", _Req())

    assert resp.status_code == 502          # upstream refused, as arranged
    assert seen["timeout"].sock_read == es._WATCH_READ_TIMEOUT_S
    assert seen["timeout"].total is None    # the stream itself stays unbounded


@pytest.mark.asyncio
async def test_health_runs_on_the_event_loop_thread():
    """`async def` here is load-bearing. A sync handler would be dispatched to
    a worker thread, putting these reads on a different thread from the
    middleware's writes — and `min()` iterates, so a dict resized mid-iteration
    raises RuntimeError, while the separate reads could straddle an insert and
    report busy with no age."""
    import inspect

    assert inspect.iscoroutinefunction(es.health)


def test_a_neighbouring_path_is_not_counted_as_work():
    """Bare `startswith` would enrol a future /watchdog or
    /agent-loop-metrics, and a high-frequency one would pin the container busy
    forever — with nothing linking the symptom to the new route's name."""
    assert es._is_work_path("/agent-loop") is True
    assert es._is_work_path("/watch/9000/index.html") is True
    assert es._is_work_path("/watch") is True
    assert es._is_work_path("/watchdog") is False
    assert es._is_work_path("/agent-loop-metrics") is False
    assert es._is_work_path("/health") is False
