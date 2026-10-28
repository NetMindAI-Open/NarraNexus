"""
@file_name: test_client_probe_settlement.py
@author:
@date: 2026-09-10
@description: The trigger paths settle a half-open probe at the client seam.

#394 review C1: the bus lane and patrol claim the circuit-breaker probe but
never go through BackgroundRun, so before this nothing settled it — a dead
credential re-ran every grant window forever with the delay never
doubling, and a repaired one never got back to ACTIVE. They now hand the
won ``probe_token`` to ``InProcessAgentRuntimeClient.run_and_collect``,
which settles it through the same record_success / record_failure the WS
path uses. These tests drive the real client and the real breaker (sqlite)
with a fake runtime.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from narranexus.platform.agent_framework.loop.circuit_breaker import (
    AUTH_QUOTA_PAUSE_THRESHOLD,
    PAUSE_HALF_OPEN_BASE_SECONDS,
    try_begin_probe,
)
from narranexus.platform.agent_runtime.cancellation import CancelledByUser
from narranexus.platform.agent_runtime.client import InProcessAgentRuntimeClient
from narranexus.platform.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from narranexus.platform.schema import (
    OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE,
    CbStatus,
    ErrorCategory,
    PausedReason,
)
from narranexus.platform.schema.runtime_message import MessageType
from narranexus.platform.utils.timezone import utc_now

AGENT = "agent_probe"


class _Msg:
    def __init__(self, wire: dict, message_type=None, **attrs):
        self._wire = wire
        self.message_type = message_type
        self.details = wire.get("details")
        self.raw = None
        for k, v in attrs.items():
            setattr(self, k, v)

    def to_dict(self) -> dict:
        return self._wire


def _reply() -> list:
    return [_Msg({"type": "agent_response", "delta": "ok"},
                 message_type=MessageType.AGENT_RESPONSE, delta="ok")]


def _fatal(error_type: str, message: str) -> list:
    return [_Msg({"type": "error", "error_type": error_type,
                  "error_message": message, "severity": "fatal"},
                 message_type=MessageType.ERROR, error_type=error_type,
                 error_message=message, severity="fatal")]


class _Runtime:
    def __init__(self, events=(), raise_exc: BaseException | None = None):
        self._events = list(events)
        self._raise = raise_exc

    def run(self, **kwargs):
        assert "probe_token" not in kwargs  # consumed by the client
        async def _gen():
            for e in self._events:
                yield e
            if self._raise is not None:
                raise self._raise
        return _gen()


@pytest.fixture
def wire(monkeypatch, db_client):
    async def _db():
        return db_client
    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _db)
    monkeypatch.setattr(cb, "get_db_client", _db)

    def set_runtime(rt):
        monkeypatch.setattr(
            "narranexus.platform.agent_runtime.agent_runtime.AgentRuntime", lambda: rt
        )
    return set_runtime


async def _paused_and_claimed(db) -> str:
    repo = AgentCircuitBreakerRepository(db)
    await repo.upsert_state(AGENT, {
        "cb_status": CbStatus.PAUSED.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": ErrorCategory.AUTH.value,
        "consecutive_failure_count": AUTH_QUOTA_PAUSE_THRESHOLD,
        "cooldown_until": utc_now() - timedelta(seconds=1),
    })
    admission = await try_begin_probe(AGENT, db=db)
    assert admission.allowed and admission.probe_token
    return admission.probe_token


async def _run(**kw):
    return await InProcessAgentRuntimeClient().run_and_collect(
        agent_id=AGENT, user_id="u", input_content="hi",
        working_source="message_bus", **kw,
    )


@pytest.mark.asyncio
async def test_a_dead_credential_probe_climbs_the_ladder(wire, db_client):
    """The probe turn ends fatal with an auth error: re-PAUSED, streak +1,
    and the next probe waits TWICE as long — the ladder the bus lane never
    climbed while nothing settled its claims."""
    token = await _paused_and_claimed(db_client)
    wire(_Runtime(_fatal("AuthenticationError", "401 invalid api key")))
    before = utc_now()
    result = await _run(probe_token=token)
    assert result.is_fatal
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD + 1
    delay = (row.cooldown_until.replace(tzinfo=None) - before.replace(tzinfo=None)).total_seconds()
    assert delay == pytest.approx(PAUSE_HALF_OPEN_BASE_SECONDS * 2, abs=2)


@pytest.mark.asyncio
async def test_a_repaired_credential_probe_closes_the_breaker(wire, db_client):
    token = await _paused_and_claimed(db_client)
    wire(_Runtime(_reply()))
    await _run(probe_token=token)
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.cb_status == CbStatus.ACTIVE.value
    assert row.probe_token is None


@pytest.mark.asyncio
async def test_a_raising_probe_is_a_failed_probe(wire, db_client):
    token = await _paused_and_claimed(db_client)
    wire(_Runtime(raise_exc=RuntimeError("401 Unauthorized: invalid x-api-key")))
    with pytest.raises(RuntimeError):
        await _run(probe_token=token)
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD + 1


@pytest.mark.asyncio
async def test_a_stopped_probe_hands_the_claim_back(wire, db_client):
    token = await _paused_and_claimed(db_client)
    wire(_Runtime(raise_exc=CancelledByUser("owner pressed stop")))
    with pytest.raises(CancelledByUser):
        await _run(probe_token=token)
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD
    assert row.probe_token is None


@pytest.mark.asyncio
async def test_an_ordinary_run_does_not_read_its_result(wire, db_client, monkeypatch):
    """#394 second review N-6: without a token run_and_collect must not read
    anything off the collection — it returns the object untouched. A result
    whose every attribute read raises proves it, whatever shape a test
    double (or a future RunCollection) takes."""
    class _Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"run_and_collect read result.{name} without a token")

    sentinel = _Untouchable()

    async def _collect(*_a, **_k):
        return sentinel

    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.run_collector.collect_run", _collect
    )
    wire(_Runtime())
    assert await _run() is sentinel


@pytest.mark.asyncio
async def test_an_ordinary_trigger_run_never_touches_the_breaker(wire, db_client):
    """No token: the trigger paths do not feed ordinary streaks (unchanged
    since before #117) — even a fatal auth failure leaves no row."""
    wire(_Runtime(_fatal("AuthenticationError", "401 invalid api key")))
    await _run()
    assert await AgentCircuitBreakerRepository(db_client).get(AGENT) is None


async def _stream(**kw) -> list:
    return [e async for e in InProcessAgentRuntimeClient().run_stream(
        agent_id=AGENT, user_id="u", input_content="hi", **kw,
    )]


@pytest.mark.asyncio
async def test_a_streamed_dead_credential_probe_climbs_the_ladder(wire, db_client):
    """#394 second review I-3: the streaming entries (NarraMessenger, A2A
    SSE) now claim too, so run_stream settles exactly like run_and_collect
    — from the same error verdict (RunErrorTracker)."""
    token = await _paused_and_claimed(db_client)
    wire(_Runtime(_fatal("AuthenticationError", "401 invalid api key")))
    await _stream(probe_token=token)
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD + 1


@pytest.mark.asyncio
async def test_a_streamed_repaired_probe_closes_the_breaker(wire, db_client):
    token = await _paused_and_claimed(db_client)
    wire(_Runtime(_reply()))
    events = await _stream(probe_token=token)
    assert len(events) == 1
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.cb_status == CbStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_a_streamed_probe_that_raises_or_stops(wire, db_client):
    token = await _paused_and_claimed(db_client)
    wire(_Runtime(raise_exc=RuntimeError("401 Unauthorized: invalid x-api-key")))
    with pytest.raises(RuntimeError):
        await _stream(probe_token=token)
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD + 1

    await AgentCircuitBreakerRepository(db_client).upsert_state(
        AGENT, {"cooldown_until": utc_now() - timedelta(seconds=1)}
    )
    token = (await try_begin_probe(AGENT, db=db_client)).probe_token
    wire(_Runtime(raise_exc=CancelledByUser("owner pressed stop")))
    with pytest.raises(CancelledByUser):
        await _stream(probe_token=token)
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD + 1
    assert row.probe_token is None


@pytest.mark.asyncio
async def test_an_ordinary_stream_never_touches_the_breaker(wire, db_client):
    wire(_Runtime(_fatal("AuthenticationError", "401 invalid api key")))
    await _stream()
    assert await AgentCircuitBreakerRepository(db_client).get(AGENT) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_an_output_budget_probe_is_released_without_verdict(wire, db_client, streamed):
    """#396 x #394: output-budget exhaustion is now a real fatal (the run is
    finalized FAILED) but exempt from the breaker. As a probe outcome it is
    no verdict on the paused credential: not a failed probe (no streak +1,
    no doubled delay) and not a success (no ACTIVE) — the claim is handed
    back, PAUSED with the same streak and the token cleared."""
    token = await _paused_and_claimed(db_client)
    wire(_Runtime(_fatal(OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE,
                         "model output truncated: thinking exhausted the output budget")))
    if streamed:
        await _stream(probe_token=token)
    else:
        result = await _run(probe_token=token)
        assert result.is_fatal
    row = await AgentCircuitBreakerRepository(db_client).get(AGENT)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD
    assert row.probe_token is None
