"""
@file_name: test_health_db_probe.py
@author: Bin Liang
@date: 2026-08-17
@description: `/health` must report the database it actually reached.

The field used to be the literal string `"database": "connected"` — it could
not take any other value. On 2026-08-17 the backend spent 19 minutes failing
every query with `InterfaceError: (0, 'Not connected')` while this endpoint
reported a connected database, the container healthcheck stayed green, and the
monitoring built on that container state never fired.

So the property under test is not "the endpoint returns 200". It is: **when the
database is unreachable, this endpoint says so, and says it loudly enough for a
container probe to notice.**
"""
from __future__ import annotations

import asyncio

import pytest

from loguru import logger

import backend.main as main


class _StubClient:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.queries: list[str] = []

    async def probe(self) -> None:
        self.queries.append("probe")
        await self._behaviour()


def _install(monkeypatch, behaviour) -> _StubClient:
    client = _StubClient(behaviour)

    async def fake_get_db_client():
        return client

    monkeypatch.setattr(main, "get_db_client", fake_get_db_client)
    return client


@pytest.mark.asyncio
async def test_reports_healthy_when_the_round_trip_succeeds(monkeypatch):
    async def ok():
        return [{"1": 1}]

    client = _install(monkeypatch, ok)

    body = await main.health()

    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert client.queries == ["probe"], "the probe must actually query the database"


@pytest.mark.asyncio
async def test_reports_unhealthy_and_503_when_the_database_is_gone(monkeypatch):
    """The exact production failure: the pool is dead, every query raises."""

    async def dead():
        raise RuntimeError("(0, 'Not connected')")

    _install(monkeypatch, dead)

    response = await main.health()

    assert response.status_code == 503, (
        "a healthy-looking 200 is what let the outage run unnoticed"
    )
    body = response.body.decode()
    assert '"status":"unhealthy"' in body.replace(" ", "")
    # The CLASS of failure reaches the reader; the driver's own message does
    # not. See the leak test below.
    assert "RuntimeError" in body


@pytest.mark.asyncio
async def test_a_hung_database_becomes_unhealthy_rather_than_a_hung_probe(monkeypatch):
    """A probe that hangs is indistinguishable from a slow one; docker would
    record its own timeout and our reason would be lost. Bound it ourselves."""

    async def hang():
        await asyncio.sleep(10)

    _install(monkeypatch, hang)
    monkeypatch.setattr(main, "_HEALTH_DB_TIMEOUT_SEC", 0.01)

    response = await main.health()

    assert response.status_code == 503
    assert "timeout" in response.body.decode()


@pytest.mark.asyncio
async def test_the_failure_reason_never_leaks_infrastructure_detail(monkeypatch):
    """`/health` is public and unauthenticated. pymysql renders connect
    failures as "Can't connect to MySQL server on '<rds endpoint>'" and auth
    failures as "Access denied for user '<user>'@'<internal ip>'". Returning
    that verbatim hands anyone who curls during an incident the database host,
    the database user, and an internal IP."""

    async def leaky():
        raise RuntimeError(
            "Can't connect to MySQL server on "
            "'narranexus-cloud-version.cluster-czi0esc0atmh.eu-west-2.rds.amazonaws.com' "
            "(Access denied for user 'nexus'@'172.31.0.4')"
        )

    _install(monkeypatch, leaky)

    response = await main.health()

    body = response.body.decode()
    assert response.status_code == 503
    for secret in ("rds.amazonaws.com", "Access denied", "172.31.", "nexus'@"):
        assert secret not in body, f"{secret!r} leaked to an unauthenticated caller"
    assert "RuntimeError" in body, "the failure class must still be reported"


@pytest.mark.asyncio
async def test_probe_budget_leaves_room_under_the_container_healthcheck_timeout():
    """`stacks/narranexus-app/compose.yml` gives the backend healthcheck
    `timeout: 5s`, and that 5s must also cover the healthcheck's own `python -c`
    interpreter start plus the urllib round-trip. A budget that merely fits
    under 5s would still let docker time out first, replacing our reason with
    its own — the precise blind spot this endpoint was fixed to close."""
    assert main._HEALTH_DB_TIMEOUT_SEC <= 3.0


@pytest.mark.asyncio
async def test_health_reports_only_the_database(monkeypatch):
    """Only the database decides `status`. The team-summary heartbeat used to
    be read from app.state here, but the worker now runs in the workers
    process (builtin.teams, backend.workers slot) and its heartbeat is its
    `[team.summary] pass:` log line there — the backend can no longer see it,
    and a block that reads state nothing sets is not a signal."""

    async def ok():
        return [{"1": 1}]

    _install(monkeypatch, ok)
    body = await main.health()
    assert body["status"] == "healthy"
    assert "team_summary" not in body


@pytest.mark.asyncio
async def test_the_driver_message_survives_in_the_log(monkeypatch):
    """The other half of the promise above: the detail the response withholds
    has to actually reach the log, or withholding it is just data loss.

    This exists because that half regressed once already. The log call sat in
    the `if not db_ok:` branch — outside the `except` — where `logger.exception`
    has no active exception to render and prints a literal "NoneType: None".
    Moving it back out would look tidier and every other test here would stay
    green, which is exactly how it shipped the first time.
    """

    async def dead():
        raise RuntimeError(
            "Can't connect to MySQL server on 'rds-x.internal' "
            "(Access denied for user 'nexus'@'172.31.0.4')"
        )

    _install(monkeypatch, dead)

    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR")
    try:
        response = await main.health()
    finally:
        logger.remove(sink_id)

    logged = "".join(records)
    assert "rds-x.internal" in logged, (
        "the driver's own message is the only place the real cause survives"
    )
    # The load-bearing assertion. The class name appears in the f-string either
    # way, so asserting on it would pass even with the call back outside the
    # except block; only a rendered traceback distinguishes the two.
    assert "Traceback" in logged, (
        "logged outside the except block — renders 'NoneType: None', no cause"
    )
    # And the response still says nothing.
    assert "rds-x.internal" not in response.body.decode()


@pytest.mark.asyncio
async def test_a_timeout_is_also_logged(monkeypatch):
    """`asyncio.TimeoutError` carries no message, so without its own log line a
    timed-out probe leaves not one word behind.

    Asserted on the log output rather than on the source text: reading the
    function body with `inspect.getsource` broke on harmless refactors (writing
    `except TimeoutError:` — the same class since 3.11 — or reordering the
    handlers) and failed with an IndexError that said nothing useful.
    """

    async def hang():
        await asyncio.sleep(10)

    _install(monkeypatch, hang)
    monkeypatch.setattr(main, "_HEALTH_DB_TIMEOUT_SEC", 0.01)

    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR")
    try:
        response = await main.health()
    finally:
        logger.remove(sink_id)

    assert response.status_code == 503
    assert "timed out after" in "".join(records)


@pytest.mark.asyncio
async def test_a_burst_of_sequential_requests_collapses_onto_one_round_trip(monkeypatch):
    """`/health` is public and unauthenticated, and each probe holds one of the
    backend's ten pooled connections. Without a cache, a few hundred requests a
    second would hold all of them while real traffic queued — an endpoint added
    to make failure visible, turned into a way to cause it.

    This covers requests arriving after a result is published. Concurrent
    arrivals before the in-flight probe publishes each run their own probe —
    that is the KNOWN LIMIT documented on `_HEALTH_CACHE_TTL_SEC` in
    `backend/main.py`, accepted on purpose, and deliberately not asserted here.
    """

    async def ok():
        return None

    client = _install(monkeypatch, ok)

    for _ in range(50):
        await main.health()

    assert len(client.queries) == 1, "every request hit the database"


@pytest.mark.asyncio
async def test_failures_are_cached_too(monkeypatch):
    """Caching only successes would leave the amplification wide open in exactly
    the case where it is dangerous: a database that is down or slow, with every
    request paying the full timeout before answering."""

    async def dead():
        raise RuntimeError("(0, 'Not connected')")

    client = _install(monkeypatch, dead)

    first = await main.health()
    for _ in range(20):
        again = await main.health()
        assert again.status_code == 503

    assert first.status_code == 503
    assert len(client.queries) == 1, "a failing database was re-probed on every request"


@pytest.mark.asyncio
async def test_the_cache_expires(monkeypatch):
    async def ok():
        return None

    client = _install(monkeypatch, ok)

    await main.health()
    assert len(client.queries) == 1

    # Expire it by rewinding the stored deadline rather than sleeping.
    started, deadline, was_ok, detail = main._health_cache
    main._health_cache = (started, deadline - main._HEALTH_CACHE_TTL_SEC - 1, was_ok, detail)

    await main.health()
    assert len(client.queries) == 2


@pytest.mark.asyncio
async def test_the_cache_window_stays_under_the_healthcheck_interval():
    """The container healthcheck runs every 30s and must never be answered from
    cache — it is the signal the container's health is derived from."""
    assert main._HEALTH_CACHE_TTL_SEC < 30.0




@pytest.mark.asyncio
async def test_concurrent_probes_publish_the_newest_verdict(monkeypatch):
    """Single-flight is gone, so probes CAN overlap, and the start-time publish
    guard in `_run_health_probe` is the ONLY thing that stops a slow failing
    probe which started before a fast succeeding one from overwriting the good
    verdict on arrival — there is no lock behind it any more.

    That is why this drives real overlapping `health()` calls end to end rather
    than restating the guard's condition: remove the guard and this test goes
    red with the exact symptom of 2026-08-17's blind spot inverted (unhealthy
    reported for a further TTL after the database recovered).
    """
    release_a = asyncio.Event()
    state = {"fail": True}

    class _Client:
        async def probe(self) -> None:
            if state["fail"]:
                await release_a.wait()
                raise RuntimeError("(0, 'Not connected')")
            return None

    async def fake_get_db_client():
        return _Client()

    monkeypatch.setattr(main, "get_db_client", fake_get_db_client)

    a = asyncio.create_task(main.health())      # starts first, still in flight
    await asyncio.sleep(0)

    state["fail"] = False                       # database recovers
    b = await main.health()                     # starts later, finishes first
    assert b["status"] == "healthy"

    release_a.set()
    await a

    assert main._health_cache[2] is True, "the older, failing probe won"
    assert (await main.health())["status"] == "healthy"
