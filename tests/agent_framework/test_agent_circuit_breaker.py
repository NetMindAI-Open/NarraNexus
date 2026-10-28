"""
@file_name: test_agent_circuit_breaker.py
@author:
@date: 2026-07-13
@description: Unit/integration tests for the real-time-layer Agent
circuit-breaker service (classification + escalation split + skip-gate +
reset), against a real in-memory sqlite.
"""

import asyncio
from datetime import timedelta

import pytest

from narranexus.platform.agent_framework.loop import circuit_breaker as cb
from narranexus.platform.agent_framework.loop.circuit_breaker import (
    AUTH_QUOTA_PAUSE_THRESHOLD,
    PAUSE_HALF_OPEN_BASE_SECONDS,
    PAUSE_HALF_OPEN_CAP_SECONDS,
    PROBE_GRANT_SECONDS,
    breaker_exemption,
    classify_agent_error,
    record_failure,
    record_success,
    release_probe,
    reset_agent,
    reset_for_owner,
    should_skip,
    try_begin_probe,
)
from narranexus.platform.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from narranexus.platform.schema import (
    EXECUTOR_INFRA_ERROR_TYPE,
    OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE,
    CbStatus,
    ErrorCategory,
    PausedReason,
)
from narranexus.platform.utils.timezone import utc_now


async def _gate(agent_id: str, db) -> tuple:
    """``should_skip``'s verdict as the (skip, reason) pair most tests assert."""
    verdict = await should_skip(agent_id, db=db)
    return (verdict.skip, verdict.reason)


async def _claim(agent_id: str, db) -> tuple:
    """``try_begin_probe``'s admission as an (allowed, reason) pair."""
    admission = await try_begin_probe(agent_id, db=db)
    return (admission.allowed, admission.reason)


async def _seed_agent(db, agent_id: str, owner: str) -> None:
    await db.insert("agents", {
        "agent_id": agent_id,
        "agent_name": agent_id,
        "created_by": owner,
    })


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def test_classify_auth():
    assert classify_agent_error("auth_expired", "login expired") == ErrorCategory.AUTH
    assert classify_agent_error("unauthorized", "") == ErrorCategory.AUTH
    assert classify_agent_error("api_error", "Incorrect API key provided") == ErrorCategory.AUTH
    assert classify_agent_error("SomeError", "HTTP 401 Unauthorized") == ErrorCategory.AUTH
    # 403 must be AUTH (owner-actionable), not leak to BUSINESS — both the
    # delimited form and a bare string-leading "403 Forbidden".
    assert classify_agent_error("SomeError", "HTTP 403 Forbidden") == ErrorCategory.AUTH
    assert classify_agent_error("X", "403 Forbidden") == ErrorCategory.AUTH


def test_classify_quota():
    for t in ("NoProviderConfiguredError", "LLMConfigNotConfigured"):
        assert classify_agent_error(t, "") == ErrorCategory.QUOTA


def test_classify_transient_is_provider_side():
    # Positively-identified provider-side signatures → TRANSIENT (notify owner).
    assert classify_agent_error("TimeoutError", "read timed out") == ErrorCategory.TRANSIENT
    assert classify_agent_error("InternalServerError", "502 bad gateway") == ErrorCategory.TRANSIENT
    assert classify_agent_error("RateLimitError", "429 too many requests") == ErrorCategory.TRANSIENT
    assert classify_agent_error("SomethingElse", "the model is overloaded") == ErrorCategory.TRANSIENT
    # "provider temporarily unavailable" must NOT be swept into AUTH by the
    # broad "provider" credential marker — transient is checked first.
    assert classify_agent_error("X", "provider temporarily unavailable") == ErrorCategory.TRANSIENT


def test_classify_business_is_the_residual():
    # Our-own bug / permanent client error / unknown → BUSINESS (platform-only).
    assert classify_agent_error("KeyError", "'foo'") == ErrorCategory.BUSINESS
    assert classify_agent_error("ValueError", "context_length_exceeded") == ErrorCategory.BUSINESS
    assert classify_agent_error("BadRequestError", "content policy violation") == ErrorCategory.BUSINESS
    assert classify_agent_error(None, None) == ErrorCategory.BUSINESS


# --------------------------------------------------------------------------
# escalation split
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_walks_backoff_then_pauses(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_auth"

    for i in range(1, AUTH_QUOTA_PAUSE_THRESHOLD):  # 1..2 → cooling
        await record_failure(aid, "auth_expired", "login expired", db=db_client)
        row = await repo.get(aid)
        assert row.cb_status == CbStatus.COOLING.value, f"strike {i} should cool"
        assert row.consecutive_failure_count == i

    # 3rd consecutive auth → PAUSED(auth)
    await record_failure(aid, "auth_expired", "login expired", db=db_client)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.paused_reason == PausedReason.AUTH.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD


@pytest.mark.asyncio
async def test_quota_pauses_with_quota_reason(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_quota"
    for _ in range(AUTH_QUOTA_PAUSE_THRESHOLD):
        await record_failure(aid, "NoProviderConfiguredError", "no provider", db=db_client)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.paused_reason == PausedReason.QUOTA.value


@pytest.mark.asyncio
async def test_self_serviceable_does_not_advance_breaker(db_client):
    """A deterministic self-serviceable failure (context window too small /
    no credits / bad model id) must NOT cool or pause. Waiting won't fix it,
    and a cooldown would block the CORRECTED retry after the user switches
    models — punishing them for following the actionable error (binding rule
    #14/#15). No row is created → should_skip stays open."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ss"
    # config_actionable marker + context-window message → skipped entirely
    await record_failure(
        aid, "config_actionable",
        "the selected model's context window is too small; must be <= 32769",
        db=db_client,
    )
    assert await repo.get(aid) is None  # no cooling/pause row created
    # raw-exception form (class name + message-only signal) also skipped
    await record_failure(
        aid, "ContextWindowExceededError", "inputs 75307 > 32769", db=db_client,
    )
    assert await repo.get(aid) is None
    assert await _gate(aid, db=db_client) == (False, None)


@pytest.mark.asyncio
async def test_executor_infra_does_not_advance_breaker(db_client):
    """A platform-side executor-infra failure (OOM / unreachable, surfaced as
    error_type=infra_transient) must NOT cool or pause. The surfaced error tells
    the user to "resend shortly"; cooling the agent would reject that very
    resend (websocket should_skip) — turning one platform blip into a
    self-inflicted second punishment (binding rule #15). No row is created →
    should_skip stays open."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_infra"
    await record_failure(
        aid, "infra_transient",
        "This turn could not run: your execution container is temporarily "
        "unreachable.",
        db=db_client,
    )
    assert await repo.get(aid) is None  # no cooling/pause row created
    await record_failure(
        aid, "infra_transient",
        "This turn could not run: the execution environment ran out of memory.",
        db=db_client,
    )
    assert await repo.get(aid) is None
    assert await _gate(aid, db=db_client) == (False, None)


@pytest.mark.asyncio
async def test_executor_infra_leaves_prior_streak_intact(db_client):
    """An executor-infra failure mid-streak must not reset or advance an
    unrelated (transient) streak — the breaker simply stays out."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_infra_mix"
    await record_failure(aid, "TimeoutError", "timeout", db=db_client)
    await record_failure(aid, "TimeoutError", "timeout", db=db_client)
    before = await repo.get(aid)
    assert before.consecutive_failure_count == 2
    await record_failure(
        aid, "infra_transient", "execution container is temporarily unreachable",
        db=db_client,
    )
    after = await repo.get(aid)
    assert after.consecutive_failure_count == 2  # unchanged
    assert after.failure_category == before.failure_category


@pytest.mark.asyncio
async def test_output_budget_exhaustion_does_not_advance_breaker(db_client):
    """A thinking model that spent its whole output budget on reasoning
    (NexusPower OUTPUT_TRUNCATED, carried as the structured
    ``output_budget_exhausted`` error_type) is deterministic for that model
    — cooling the agent would only reject the user's next message (binding
    rule #15)."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_budget"
    message = (
        "model output truncated: thinking exhausted the output budget "
        "(max_tokens=8192)"
    )
    await record_failure(aid, OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE, message, db=db_client)
    await record_failure(aid, OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE, message, db=db_client)
    assert await repo.get(aid) is None  # no cooling/pause row created
    verdict = await should_skip(aid, db=db_client)
    assert verdict.skip is False and verdict.reason is None


@pytest.mark.asyncio
async def test_budget_phrase_in_message_alone_does_not_exempt(db_client):
    """The exemption is keyed on the structured error_type, never on message
    text: a provider error that merely echoes the phrase (caller-controlled
    content) must still advance the breaker."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_echo"
    await record_failure(
        aid,
        "invalid_request",
        "400 bad request: prompt contained 'model output truncated: thinking "
        "exhausted the output budget (max_tokens=8192)'",
        db=db_client,
    )
    row = await repo.get(aid)
    assert row is not None
    assert row.cb_status == CbStatus.COOLING.value
    assert row.consecutive_failure_count == 1


def test_breaker_exemptions_name_each_class_and_nothing_else():
    """One table lists every failure the breaker ignores; anything else
    advances it."""
    assert breaker_exemption(
        "config_actionable", "the model's maximum context length is 8192 tokens"
    ) == "self-serviceable"
    assert breaker_exemption(
        EXECUTOR_INFRA_ERROR_TYPE, "out of memory"
    ) == "executor-infra (platform-side)"
    assert breaker_exemption(
        OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE, ""
    ) == "output-budget exhaustion"
    assert breaker_exemption("TimeoutError", "read timed out") is None
    assert breaker_exemption(
        "invalid_request", "thinking exhausted the output budget"
    ) is None


@pytest.mark.asyncio
async def test_self_serviceable_leaves_prior_streak_intact(db_client):
    """A self-serviceable failure mid-streak must not reset or advance an
    unrelated (transient) streak — the breaker simply stays out."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_mix"
    await record_failure(aid, "TimeoutError", "timeout", db=db_client)
    await record_failure(aid, "TimeoutError", "timeout", db=db_client)
    before = await repo.get(aid)
    assert before.consecutive_failure_count == 2
    # self-serviceable error in between — breaker untouched
    await record_failure(
        aid, "config_actionable",
        "the model's maximum context length is 8192 tokens", db=db_client,
    )
    after = await repo.get(aid)
    assert after.consecutive_failure_count == 2  # unchanged
    assert after.failure_category == before.failure_category


@pytest.mark.asyncio
async def test_transient_never_pauses_and_backoff_grows(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_trans"
    prev_cd = None
    for i in range(1, 13):
        await record_failure(aid, "TimeoutError", "timeout", db=db_client)
        row = await repo.get(aid)
        assert row.cb_status == CbStatus.COOLING.value, f"transient never pauses (i={i})"
        assert row.consecutive_failure_count == i
        # cooldown grows monotonically then plateaus (never regresses).
        cd = row.cooldown_until
        if prev_cd is not None:
            assert cd >= prev_cd
        prev_cd = cd


@pytest.mark.asyncio
async def test_sustained_transient_notifies_owner_not_internal(db_client, monkeypatch):
    calls = {"transient": [], "internal": []}

    async def fake_transient(**kw):
        calls["transient"].append(kw)

    async def fake_internal(**kw):
        calls["internal"].append(kw)

    monkeypatch.setattr(cb, "alert_agent_transient_streak", fake_transient)
    monkeypatch.setattr(cb, "audit_agent_internal_streak", fake_internal)

    for _ in range(5):
        await record_failure("ag_t", "TimeoutError", "read timed out", db=db_client)

    # Exactly one owner-facing transient alert at the 5th strike; no internal.
    assert len(calls["transient"]) == 1
    assert calls["transient"][0]["consecutive_failures"] == 5
    assert calls["internal"] == []
    # Never paused.
    assert (await AgentCircuitBreakerRepository(db_client).get("ag_t")).cb_status == CbStatus.COOLING.value


@pytest.mark.asyncio
async def test_sustained_business_stays_internal_not_owner(db_client, monkeypatch):
    calls = {"transient": [], "internal": []}

    async def fake_transient(**kw):
        calls["transient"].append(kw)

    async def fake_internal(**kw):
        calls["internal"].append(kw)

    monkeypatch.setattr(cb, "alert_agent_transient_streak", fake_transient)
    monkeypatch.setattr(cb, "audit_agent_internal_streak", fake_internal)

    # A pipeline bug (KeyError) → BUSINESS: platform-only, never the owner.
    for _ in range(5):
        await record_failure("ag_b", "KeyError", "'foo'", db=db_client)

    assert len(calls["internal"]) == 1
    assert calls["transient"] == []
    # BUSINESS never pauses either.
    assert (await AgentCircuitBreakerRepository(db_client).get("ag_b")).cb_status == CbStatus.COOLING.value


@pytest.mark.asyncio
async def test_category_change_resets_streak(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_mixed"
    await record_failure(aid, "auth_expired", "x", db=db_client)   # auth count 1
    await record_failure(aid, "auth_expired", "x", db=db_client)   # auth count 2
    await record_failure(aid, "TimeoutError", "x", db=db_client)   # transient → reset to 1
    row = await repo.get(aid)
    assert row.failure_category == ErrorCategory.TRANSIENT.value
    assert row.consecutive_failure_count == 1
    await record_failure(aid, "auth_expired", "x", db=db_client)   # auth → reset to 1
    row = await repo.get(aid)
    assert row.failure_category == ErrorCategory.AUTH.value
    assert row.consecutive_failure_count == 1
    assert row.cb_status == CbStatus.COOLING.value  # not paused — streak only 1


@pytest.mark.asyncio
async def test_single_auth_then_success_self_heals(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_blip"
    await record_failure(aid, "auth_expired", "blip", db=db_client)
    assert (await repo.get(aid)).cb_status == CbStatus.COOLING.value
    await record_success(aid, db=db_client)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.ACTIVE.value
    assert row.consecutive_failure_count == 0
    assert row.failure_category is None


# --------------------------------------------------------------------------
# skip-gate
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_should_skip_states(db_client):
    repo = AgentCircuitBreakerRepository(db_client)

    # missing row → allow
    assert await _gate("ghost", db=db_client) == (False, None)

    # paused, half-open delay still running → skip
    await repo.upsert_state("p", {"cb_status": CbStatus.PAUSED.value,
                                  "paused_reason": PausedReason.AUTH.value,
                                  "cooldown_until": utc_now() + timedelta(minutes=5)})
    skip, reason = await _gate("p", db=db_client)
    assert skip and reason.startswith("paused:auth")

    # paused with NO deadline at all → fail-safe toward the probe (a row
    # nobody could ever probe would be a permanent dead end).
    await repo.upsert_state("p_null", {"cb_status": CbStatus.PAUSED.value,
                                       "paused_reason": PausedReason.AUTH.value})
    assert await _gate("p_null", db=db_client) == (False, None)

    # cooling in the future → skip
    await repo.upsert_state("c_future", {
        "cb_status": CbStatus.COOLING.value,
        "cooldown_until": utc_now() + timedelta(minutes=5),
    })
    assert await _gate("c_future", db=db_client) == (True, "cooling")

    # cooling already elapsed → allow (lazy expiry)
    await repo.upsert_state("c_past", {
        "cb_status": CbStatus.COOLING.value,
        "cooldown_until": utc_now() - timedelta(minutes=5),
    })
    assert await _gate("c_past", db=db_client) == (False, None)


# --------------------------------------------------------------------------
# half-open (GitHub #117: PAUSED must not be a dead end)
# --------------------------------------------------------------------------

def _paused_row(**overrides) -> dict:
    row = {
        "cb_status": CbStatus.PAUSED.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": ErrorCategory.AUTH.value,
        "consecutive_failure_count": AUTH_QUOTA_PAUSE_THRESHOLD,
        "cooldown_until": utc_now() - timedelta(seconds=1),  # window open
        "probe_token": None,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_paused_before_timeout_stays_skipped(db_client):
    """A PAUSED agent whose half-open delay has NOT elapsed yet must be
    skipped by the read gate AND refused by the claim."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_early"
    await repo.upsert_state(aid, _paused_row(cooldown_until=utc_now() + timedelta(minutes=5)))
    skip, reason = await _gate(aid, db=db_client)
    assert skip and reason.startswith("paused:auth")
    allowed, reason = await _claim(aid, db_client)
    assert allowed is False and reason == "paused:auth"
    assert (await repo.get(aid)).cb_status == CbStatus.PAUSED.value


@pytest.mark.asyncio
async def test_should_skip_is_a_pure_read_and_never_claims(db_client):
    """The read gate must NOT flip the row: any caller may ask and then not
    run a turn (the bus poller's @mention filter does exactly that every
    3s), so the single probe grant must survive should_skip untouched."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_read"
    await repo.upsert_state(aid, _paused_row())
    for _ in range(3):
        assert await _gate(aid, db=db_client) == (False, None)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.probe_token is None


@pytest.mark.asyncio
async def test_try_begin_probe_claims_exactly_once(db_client):
    """The claim flips PAUSED→PROBING, stamps a fresh probe_token and the
    grant expiry; a second sequential caller loses with the "probing" copy
    (never "go re-login" — someone is testing the credential right now)."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho"
    await repo.upsert_state(aid, _paused_row())
    before = utc_now()
    assert await _claim(aid, db_client) == (True, None)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PROBING.value
    assert row.probe_token
    grant = (row.cooldown_until.replace(tzinfo=None) - before.replace(tzinfo=None)).total_seconds()
    assert grant == pytest.approx(PROBE_GRANT_SECONDS, abs=2)
    assert await _claim(aid, db_client) == (False, "probing")
    assert await _gate(aid, db=db_client) == (True, "probing")


@pytest.mark.asyncio
async def test_concurrent_claims_on_open_window_let_exactly_one_through(db_client):
    """Racing callers on the same expired PAUSED row: the CAS lets ONE win.
    Concurrent (gather), not sequential — a naive read-then-write claim
    interleaves at the awaits and lets all of them through."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_race"
    await repo.upsert_state(aid, _paused_row())
    results = await asyncio.gather(*[_claim(aid, db_client) for _ in range(4)])
    assert results.count((True, None)) == 1
    assert results.count((False, "probing")) == 3


@pytest.mark.asyncio
async def test_concurrent_reclaims_of_stale_probing_let_exactly_one_through(db_client):
    """The stale-PROBING self-heal writes cb_status='probing' over
    cb_status='probing', so a status-only filter matched every racer; the
    probe_token CAS is what makes this branch single-winner."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_stale_race"
    await repo.upsert_state(aid, _paused_row(
        cb_status=CbStatus.PROBING.value, probe_token="old-token",
    ))
    results = await asyncio.gather(*[_claim(aid, db_client) for _ in range(4)])
    assert results.count((True, None)) == 1
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PROBING.value
    assert row.probe_token and row.probe_token != "old-token"


@pytest.mark.asyncio
async def test_probing_row_with_expired_grant_self_heals(db_client):
    """A probe that never reported back (grant expired, no live run) must
    not jam the breaker open forever — the next claim re-stamps the grant."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_stale"
    await repo.upsert_state(aid, _paused_row(
        cb_status=CbStatus.PROBING.value, probe_token="dead-probe",
    ))
    assert await _gate(aid, db=db_client) == (False, None)
    before = utc_now()
    assert await _claim(aid, db_client) == (True, None)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PROBING.value
    assert row.probe_token != "dead-probe"
    grant = (row.cooldown_until.replace(tzinfo=None) - before.replace(tzinfo=None)).total_seconds()
    assert grant == pytest.approx(PROBE_GRANT_SECONDS, abs=2)


@pytest.mark.asyncio
async def test_expired_grant_with_live_run_is_not_reclaimed(db_client):
    """Binding rule #14: a probe turn may run for hours. An expired grant
    whose run still has a fresh heartbeat is NOT abandoned — re-claiming it
    would double-probe the same dead credential."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_long"
    await repo.upsert_state(aid, _paused_row(
        cb_status=CbStatus.PROBING.value, probe_token="long-probe",
        probe_run_id="evt_long_probe",
    ))
    await db_client.insert("events", {
        "event_id": "evt_long_probe",
        "agent_id": aid,
        "user_id": "u",
        "trigger": "chat",
        "trigger_source": "websocket",
        "state": "running",
        "started_at": utc_now() - timedelta(hours=3),
        "last_event_at": utc_now(),  # fresh heartbeat
    })
    assert await _claim(aid, db_client) == (False, "probing")
    assert (await repo.get(aid)).probe_token == "long-probe"
    # Once the heartbeat is stale the run counts as dead → re-claimable.
    await db_client.update("events", {"event_id": "evt_long_probe"},
                           {"last_event_at": utc_now() - timedelta(hours=1)})
    assert await _claim(aid, db_client) == (True, None)


@pytest.mark.asyncio
async def test_probing_row_with_live_grant_stays_skipped(db_client):
    """A probe still within its grant window must reject every other caller
    (this IS the single-probe guarantee, from a fresh caller's POV)."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_live"
    await repo.upsert_state(aid, _paused_row(
        cb_status=CbStatus.PROBING.value, probe_token="t",
        cooldown_until=utc_now() + timedelta(minutes=5),
    ))
    assert await _gate(aid, db=db_client) == (True, "probing")
    assert await _claim(aid, db_client) == (False, "probing")


@pytest.mark.asyncio
async def test_cas_write_failure_counts_as_not_claimed(db_client, monkeypatch):
    """A failed CAS WRITE is fail-CLOSED for the probe (nobody won), unlike
    a failed READ which stays fail-open. Letting a known-dead agent through
    whenever the DB is unhealthy is the wrong failure direction."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_wfail"
    await repo.upsert_state(aid, _paused_row())

    async def boom(self, *a, **k):
        raise RuntimeError("lock wait timeout")
    monkeypatch.setattr(AgentCircuitBreakerRepository, "try_claim_probe", boom)

    assert await _claim(aid, db_client) == (False, "probing")
    assert (await repo.get(aid)).cb_status == CbStatus.PAUSED.value


@pytest.mark.asyncio
async def test_half_open_probe_success_closes_breaker(db_client):
    """The claimed probe turn succeeds -> record_success clears PROBING to
    ACTIVE (and the probe_token), same clean-state path as any success."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_ok"
    await repo.upsert_state(aid, _paused_row())
    adm = await try_begin_probe(aid, db=db_client)
    assert adm.allowed and adm.probe_token
    await record_success(aid, db=db_client, probe_token=adm.probe_token)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.ACTIVE.value
    assert row.consecutive_failure_count == 0
    assert row.probe_token is None


@pytest.mark.asyncio
async def test_half_open_probe_failure_repauses_with_longer_timeout(db_client, monkeypatch):
    """The claimed probe turn fails again (still auth) -> re-PAUSE with a
    STRICTLY LONGER half-open delay than the first pause (doubling), the
    probe_token cleared, and NO second owner alert (same outage)."""
    alerts = []

    async def fake_alert(**kw):
        alerts.append(kw)
    monkeypatch.setattr(cb, "alert_agent_paused", fake_alert)

    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_fail"
    for _ in range(AUTH_QUOTA_PAUSE_THRESHOLD):
        await record_failure(aid, "auth_expired", "login expired", db=db_client)
    first_paused = await repo.get(aid)
    assert first_paused.cb_status == CbStatus.PAUSED.value
    first_delay = (first_paused.cooldown_until - first_paused.paused_at).total_seconds()
    assert first_delay == pytest.approx(PAUSE_HALF_OPEN_BASE_SECONDS, abs=2)
    assert len(alerts) == 1

    await repo.upsert_state(aid, {"cooldown_until": utc_now() - timedelta(seconds=1)})
    adm = await try_begin_probe(aid, db=db_client)
    assert adm.allowed and adm.probe_token

    before_fail = utc_now()
    await record_failure(aid, "auth_expired", "still dead", db=db_client, probe_token=adm.probe_token)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.probe_token is None
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD + 1
    second_delay = (row.cooldown_until.replace(tzinfo=None) - before_fail.replace(tzinfo=None)).total_seconds()
    assert second_delay > first_delay
    assert second_delay == pytest.approx(PAUSE_HALF_OPEN_BASE_SECONDS * 2, abs=2)
    assert len(alerts) == 1  # not re-alerted for the same outage


@pytest.mark.asyncio
async def test_probe_failing_for_a_non_auth_reason_keeps_the_pause(db_client):
    """A probe that lands on a transient blip proves nothing about the key:
    the row stays PAUSED (never COOLING), streak/category/reason unchanged,
    and the SAME half-open delay is re-armed — not doubled (rule #15: a
    network hiccup must not push the owner toward the 6h cap)."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "ag_ho_blip"
    for _ in range(AUTH_QUOTA_PAUSE_THRESHOLD):
        await record_failure(aid, "auth_expired", "login expired", db=db_client)
    await repo.upsert_state(aid, {"cooldown_until": utc_now() - timedelta(seconds=1)})
    adm = await try_begin_probe(aid, db=db_client)
    assert adm.allowed and adm.probe_token

    before = utc_now()
    await record_failure(aid, "TimeoutError", "read timed out", db=db_client, probe_token=adm.probe_token)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.paused_reason == PausedReason.AUTH.value
    assert row.failure_category == ErrorCategory.AUTH.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD
    assert row.probe_token is None
    delay = (row.cooldown_until.replace(tzinfo=None) - before.replace(tzinfo=None)).total_seconds()
    assert delay == pytest.approx(PAUSE_HALF_OPEN_BASE_SECONDS, abs=2)
    # and the gate is closed again until that delay elapses
    assert (await _gate(aid, db=db_client))[0] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type,message", [
    ("config_actionable", "the selected model's context window is too small; must be <= 32769"),
    ("infra_transient", "This turn could not run: your execution container is temporarily unreachable."),
    (OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE,
     "model output truncated: thinking exhausted the output budget (max_tokens=8192)"),
])
async def test_exempt_failures_still_settle_a_probing_row(db_client, error_type, message):
    """Every breaker exemption (self-serviceable / executor-infra /
    output-budget exhaustion) leaves a streak untouched — but a held probe
    must still be released back to PAUSED without a verdict (same streak,
    same delay, token cleared), or it hangs until the grant expires. The
    exemption must not read as a probe success either: an output-budget
    failure says nothing about whether the paused credential works."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = f"ag_ho_exempt_{error_type}"
    await repo.upsert_state(aid, _paused_row())
    adm = await try_begin_probe(aid, db=db_client)
    assert adm.allowed and adm.probe_token
    await record_failure(aid, error_type, message, db=db_client, probe_token=adm.probe_token)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD
    assert row.probe_token is None
    assert not _elapsed_now(row.cooldown_until)


def _elapsed_now(value) -> bool:
    return value.replace(tzinfo=None) <= utc_now().replace(tzinfo=None)


@pytest.mark.asyncio
async def test_release_probe_settles_only_its_own_claim(db_client):
    """release_probe is keyed on the claim's identity: a wrong or missing
    token is a no-op (the row stays PROBING under its real claimant), the
    right one hands the probe back (→ PAUSED, same delay), and a repeat is a
    no-op — so every admitted turn may call it unconditionally at its end.
    A row that is not PROBING is never touched."""
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("rel_p", _paused_row(cb_status=CbStatus.PROBING.value, probe_token="t"))
    await repo.upsert_state("rel_c", {"cb_status": CbStatus.COOLING.value,
                                      "consecutive_failure_count": 1,
                                      "cooldown_until": utc_now() + timedelta(minutes=1)})
    assert await release_probe("rel_p", "someone-else", db=db_client) is False
    assert await release_probe("rel_p", None, db=db_client) is False
    assert (await repo.get("rel_p")).probe_token == "t"

    assert await release_probe("rel_p", "t", db=db_client) is True
    row = await repo.get("rel_p")
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.probe_token is None
    assert row.probe_run_id is None
    assert not _elapsed_now(row.cooldown_until)
    assert await release_probe("rel_p", "t", db=db_client) is False  # idempotent

    assert await release_probe("rel_c", "t", db=db_client) is False
    assert (await repo.get("rel_c")).cb_status == CbStatus.COOLING.value
    assert await release_probe("rel_missing", "t", db=db_client) is False


async def _insert_running(db, event_id: str, agent_id: str, *, started_at, last_event_at) -> None:
    await db.insert("events", {
        "event_id": event_id,
        "agent_id": agent_id,
        "user_id": "u",
        "trigger": "chat",
        "trigger_source": "websocket",
        "state": "running",
        "started_at": started_at,
        "last_event_at": last_event_at,
    })


@pytest.mark.asyncio
async def test_a_long_run_that_predates_the_claim_cannot_weld_the_probe(db_client):
    """#394 review I1: run A (hours long, binding rule #14) is live; run C
    claims the probe and is cancelled. C's release carries its token, so A's
    liveness is irrelevant — the row goes back to PAUSED instead of staying
    PROBING for as long as A runs. And once the grant is stale, A still does
    not block a re-claim: only a run that started after the claim can be
    the claimant."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "weld"
    await repo.upsert_state(aid, _paused_row())
    await _insert_running(db_client, "evt_weld_a", aid,
                          started_at=utc_now() - timedelta(hours=3),
                          last_event_at=utc_now())
    adm = await try_begin_probe(aid, db=db_client)
    assert adm.allowed and adm.probe_token
    # A fresh claim names no claimant run until its run row exists.
    assert (await repo.get(aid)).probe_run_id is None

    assert await release_probe(aid, adm.probe_token, db=db_client) is True
    assert (await repo.get(aid)).cb_status == CbStatus.PAUSED.value

    # A crashed claimant (no token to release with): the stale grant is
    # re-claimable although A is still beating.
    await repo.upsert_state(aid, {"cooldown_until": utc_now() - timedelta(seconds=1)})
    lost = await try_begin_probe(aid, db=db_client)
    assert lost.allowed
    await repo.upsert_state(aid, {"cooldown_until": utc_now() - timedelta(seconds=1)})
    again = await try_begin_probe(aid, db=db_client)
    assert again.allowed and again.probe_token != lost.probe_token


@pytest.mark.asyncio
async def test_expired_grant_with_a_live_bound_claimant_is_not_reclaimed(db_client):
    """The counterpart: while the run the claimant bound is heartbeat-fresh,
    the probe is still running (binding rule #14: it may run for hours), so
    its stale grant is not re-claimed; once that run's heartbeat dies it is."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "claimant_live"
    await _insert_running(db_client, "evt_claimant", aid,
                          started_at=utc_now() - timedelta(minutes=30),
                          last_event_at=utc_now())
    await repo.upsert_state(aid, _paused_row(
        cb_status=CbStatus.PROBING.value, probe_token="c", probe_run_id="evt_claimant",
    ))
    assert await _claim(aid, db_client) == (False, "probing")
    assert await cb.release_orphaned_probe(aid, db=db_client) is False
    await db_client.update("events", {"event_id": "evt_claimant"},
                           {"last_event_at": utc_now() - timedelta(hours=1)})
    assert await cb.release_orphaned_probe(aid, db=db_client) is True
    assert (await repo.get(aid)).cb_status == CbStatus.PAUSED.value


@pytest.mark.asyncio
async def test_unrelated_runs_started_after_the_claim_cannot_weld_the_probe(db_client):
    """#394 second review I-1: the claimant is identified by the run it
    bound, not by time. Its run died (crash, then the sweep flipped it);
    other runs of the agent that started AFTER the claim — an ungated IM
    channel's turns arriving back to back — are alive, and must not keep
    the row PROBING: both the sweep's release and a re-claim go through."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "weld_newer"
    claimed = utc_now() - timedelta(minutes=10)
    await _insert_running(db_client, "evt_dead_claimant", aid,
                          started_at=claimed, last_event_at=claimed)
    await db_client.update("events", {"event_id": "evt_dead_claimant"}, {"state": "failed"})
    for i in range(3):
        await _insert_running(db_client, f"evt_im_{i}", aid,
                              started_at=claimed + timedelta(minutes=i + 1),
                              last_event_at=utc_now())
    await repo.upsert_state(aid, _paused_row(
        cb_status=CbStatus.PROBING.value, probe_token="dead",
        probe_run_id="evt_dead_claimant",
        cooldown_until=utc_now() + timedelta(minutes=3),
    ))
    assert await cb.release_orphaned_probe(aid, db=db_client) is True
    assert (await repo.get(aid)).cb_status == CbStatus.PAUSED.value

    # Same row, grant expired instead of swept: the next turn re-claims.
    await repo.upsert_state(aid, {
        "cb_status": CbStatus.PROBING.value, "probe_token": "dead",
        "probe_run_id": "evt_dead_claimant",
        "cooldown_until": utc_now() - timedelta(seconds=1),
    })
    adm = await try_begin_probe(aid, db=db_client)
    assert adm.allowed and adm.probe_token and adm.probe_token != "dead"
    assert (await repo.get(aid)).probe_run_id is None


@pytest.mark.asyncio
async def test_an_unbound_claim_lives_exactly_as_long_as_its_grant(db_client):
    """Between the claim and the claimant's run row nothing is bound: the
    claimant may still be on its way while the grant lasts (the sweep must
    not release it on behalf of some other lost run), and is presumed gone
    once the grant has expired — whatever other runs the agent has."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "unbound"
    await _insert_running(db_client, "evt_unbound_other", aid,
                          started_at=utc_now(), last_event_at=utc_now())
    await repo.upsert_state(aid, _paused_row(
        cb_status=CbStatus.PROBING.value, probe_token="pending",
        cooldown_until=utc_now() + timedelta(minutes=3),
    ))
    assert await cb.release_orphaned_probe(aid, db=db_client) is False
    assert await _claim(aid, db_client) == (False, "probing")
    await repo.upsert_state(aid, {"cooldown_until": utc_now() - timedelta(seconds=1)})
    assert await cb.release_orphaned_probe(aid, db=db_client) is True
    assert (await repo.get(aid)).cb_status == CbStatus.PAUSED.value


@pytest.mark.asyncio
async def test_bind_probe_run_needs_the_live_token_and_keeps_probing(db_client):
    """The claimant names its run through the token CAS: a wrong or missing
    token binds nothing, a bind never moves the row out of PROBING, and a
    settled claim can no longer be bound."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "bind"
    await repo.upsert_state(aid, _paused_row())
    adm = await try_begin_probe(aid, db=db_client)
    assert adm.probe_token
    assert await cb.bind_probe_run(aid, "not-mine", "evt_x", db=db_client) is False
    assert await cb.bind_probe_run(aid, None, "evt_x", db=db_client) is False
    assert (await repo.get(aid)).probe_run_id is None

    assert await cb.bind_probe_run(aid, adm.probe_token, "evt_mine", db=db_client) is True
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PROBING.value
    assert row.probe_token == adm.probe_token and row.probe_run_id == "evt_mine"

    assert await release_probe(aid, adm.probe_token, db=db_client) is True
    row = await repo.get(aid)
    assert row.probe_run_id is None
    assert await cb.bind_probe_run(aid, adm.probe_token, "evt_late", db=db_client) is False
    assert (await repo.get(aid)).probe_run_id is None


@pytest.mark.asyncio
async def test_a_turn_that_did_not_claim_cannot_settle_the_probe(db_client, monkeypatch):
    """#394 review I1/I5: while another turn holds the probe, a success or a
    failure from a turn WITHOUT the token (a run that predates the claim, an
    entry that never claimed) must not decide it — the row stays PROBING
    under its claimant, streak untouched. The claimant then settles it."""
    alerts = []

    async def fake_alert(**kw):
        alerts.append(kw)
    monkeypatch.setattr(cb, "alert_agent_paused", fake_alert)

    repo = AgentCircuitBreakerRepository(db_client)
    aid = "not_mine"
    await repo.upsert_state(aid, _paused_row())
    adm = await try_begin_probe(aid, db=db_client)
    assert adm.allowed and adm.probe_token

    await record_success(aid, db=db_client)
    await record_failure(aid, "auth_expired", "login expired", db=db_client)
    await record_success(aid, db=db_client, probe_token="stale-token")
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PROBING.value
    assert row.probe_token == adm.probe_token
    assert row.consecutive_failure_count == AUTH_QUOTA_PAUSE_THRESHOLD

    await record_success(aid, db=db_client, probe_token=adm.probe_token)
    assert (await repo.get(aid)).cb_status == CbStatus.ACTIVE.value
    assert alerts == []


@pytest.mark.asyncio
async def test_a_probe_verdict_does_not_land_on_a_row_reset_meanwhile(db_client, monkeypatch):
    """The probe's failure is written by the token CAS: if the owner reset
    the row between the settlement's read and its write, the (now stale)
    verdict must not re-pause a breaker the owner just cleared."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = "reset_mid_probe"
    await repo.upsert_state(aid, _paused_row())
    adm = await try_begin_probe(aid, db=db_client)

    real_get = AgentCircuitBreakerRepository.get

    async def get_then_owner_resets(self, agent_id):
        row = await real_get(self, agent_id)
        await self.upsert_state(agent_id, dict(cb._CLEAN_STATE))
        return row
    monkeypatch.setattr(AgentCircuitBreakerRepository, "get", get_then_owner_resets)
    await record_failure(aid, "auth_expired", "still dead", db=db_client,
                         probe_token=adm.probe_token)
    monkeypatch.setattr(AgentCircuitBreakerRepository, "get", real_get)

    row = await repo.get(aid)
    assert row.cb_status == CbStatus.ACTIVE.value
    assert row.consecutive_failure_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("succeeded,error_type,expected_status,expected_count", [
    (True, None, CbStatus.ACTIVE.value, 0),
    (False, "auth_expired", CbStatus.PAUSED.value, AUTH_QUOTA_PAUSE_THRESHOLD + 1),
    (False, "TimeoutError", CbStatus.PAUSED.value, AUTH_QUOTA_PAUSE_THRESHOLD),
    (None, None, CbStatus.PAUSED.value, AUTH_QUOTA_PAUSE_THRESHOLD),
])
async def test_settle_probe_settles_through_the_shared_record_path(
    db_client, succeeded, error_type, expected_status, expected_count
):
    """The trigger paths' settlement seam: success closes the breaker, an
    auth failure re-pauses with streak +1 (so the delay doubles — the ladder
    the bus lane never climbed before #394 C1), a non-auth failure and a
    no-verdict both re-arm the same pause."""
    repo = AgentCircuitBreakerRepository(db_client)
    aid = f"settle_{succeeded}_{error_type}"
    await repo.upsert_state(aid, _paused_row())
    adm = await try_begin_probe(aid, db=db_client)
    await cb.settle_probe(
        aid, adm.probe_token, succeeded=succeeded,
        error_type=error_type, error_message=error_type or "", db=db_client,
    )
    row = await repo.get(aid)
    assert row.cb_status == expected_status
    assert row.consecutive_failure_count == expected_count
    assert row.probe_token is None


@pytest.mark.asyncio
async def test_settle_probe_without_a_claim_leaves_the_breaker_alone(db_client):
    """The trigger paths record probe outcomes ONLY: an ordinary turn (no
    token) never feeds a streak there, exactly as before #117."""
    repo = AgentCircuitBreakerRepository(db_client)
    await cb.settle_probe("ordinary", None, succeeded=False,
                          error_type="auth_expired", error_message="x", db=db_client)
    assert await repo.get("ordinary") is None


class _CountingDb:
    """Delegates to a real client and counts breaker-table reads."""

    def __init__(self, inner):
        self._inner = inner
        self.breaker_reads = 0

    async def get_one(self, table, filters, *a, **k):
        if table == AgentCircuitBreakerRepository.table_name:
            self.breaker_reads += 1
        return await self._inner.get_one(table, filters, *a, **k)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_claim_reuses_the_gate_read(db_client):
    """#394 review M1: an entry point's should_skip read is handed to
    try_begin_probe (prior=), so an ordinary turn costs ONE breaker read."""
    db = _CountingDb(db_client)
    verdict = await should_skip("m1_active", db=db)
    admission = await try_begin_probe("m1_active", db=db, prior=verdict)
    assert admission.allowed and admission.probe_token is None
    assert db.breaker_reads == 1


@pytest.mark.asyncio
async def test_exempt_failure_of_an_ordinary_turn_reads_nothing(db_client):
    """#394 review M2: the breaker exemptions (self-serviceable /
    executor-infra / output-budget exhaustion) are decided before any read,
    so the common exempt failure costs no round trip; only a probe holder
    reads, to settle."""
    db = _CountingDb(db_client)
    await record_failure(
        "m2", "ContextWindowExceededError", "inputs 75307 > 32769", db=db,
    )
    await record_failure(
        "m2", OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE, "thinking exhausted the budget", db=db,
    )
    assert db.breaker_reads == 0


def test_half_open_delay_doubles_then_caps():
    assert cb._compute_half_open_delay_seconds(AUTH_QUOTA_PAUSE_THRESHOLD) == PAUSE_HALF_OPEN_BASE_SECONDS
    assert cb._compute_half_open_delay_seconds(AUTH_QUOTA_PAUSE_THRESHOLD + 1) == PAUSE_HALF_OPEN_BASE_SECONDS * 2
    assert cb._compute_half_open_delay_seconds(AUTH_QUOTA_PAUSE_THRESHOLD + 7) == PAUSE_HALF_OPEN_CAP_SECONDS
    # a chronically failing agent: still the cap, and no runaway exponent
    assert cb._compute_half_open_delay_seconds(10_000) == PAUSE_HALF_OPEN_CAP_SECONDS


# --------------------------------------------------------------------------
# reset / auto-resume
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_agent(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("r", {"cb_status": CbStatus.PAUSED.value,
                                  "paused_reason": PausedReason.AUTH.value,
                                  "consecutive_failure_count": 3})
    await reset_agent("r", db=db_client)
    row = await repo.get("r")
    assert row.cb_status == CbStatus.ACTIVE.value
    assert row.consecutive_failure_count == 0


@pytest.mark.asyncio
async def test_reset_for_owner_selective(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    await _seed_agent(db_client, "own_auth", "alice")
    await _seed_agent(db_client, "own_trans", "alice")
    await _seed_agent(db_client, "other", "bob")

    # alice: auth-paused + transient-cooling; bob: auth-paused
    await repo.upsert_state("own_auth", {"cb_status": CbStatus.PAUSED.value,
                                         "paused_reason": PausedReason.AUTH.value,
                                         "failure_category": ErrorCategory.AUTH.value})
    await repo.upsert_state("own_trans", {"cb_status": CbStatus.COOLING.value,
                                          "failure_category": ErrorCategory.TRANSIENT.value,
                                          "cooldown_until": utc_now() + timedelta(minutes=5)})
    await repo.upsert_state("other", {"cb_status": CbStatus.PAUSED.value,
                                      "paused_reason": PausedReason.AUTH.value,
                                      "failure_category": ErrorCategory.AUTH.value})

    n = await reset_for_owner("alice", db=db_client)
    assert n == 1  # only the auth-paused agent; transient-cooling left alone

    assert (await repo.get("own_auth")).cb_status == CbStatus.ACTIVE.value
    assert (await repo.get("own_trans")).cb_status == CbStatus.COOLING.value  # untouched
    assert (await repo.get("other")).cb_status == CbStatus.PAUSED.value       # other owner


@pytest.mark.asyncio
async def test_reset_for_owner_clears_probing(db_client):
    """A reconfigure lands while the owner's agent is mid-probe: the PROBING
    row is cleared to ACTIVE (with its probe_token), another owner's is not."""
    repo = AgentCircuitBreakerRepository(db_client)
    await _seed_agent(db_client, "own_probe", "dave")
    await _seed_agent(db_client, "other_probe", "erin")
    probing = _paused_row(cb_status=CbStatus.PROBING.value, probe_token="t",
                          cooldown_until=utc_now() + timedelta(minutes=5))
    await repo.upsert_state("own_probe", probing)
    await repo.upsert_state("other_probe", probing)
    assert await reset_for_owner("dave", db=db_client) == 1
    row = await repo.get("own_probe")
    assert row.cb_status == CbStatus.ACTIVE.value
    assert row.probe_token is None
    assert (await repo.get("other_probe")).cb_status == CbStatus.PROBING.value


@pytest.mark.asyncio
async def test_reset_for_owner_clears_authquota_cooling(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    await _seed_agent(db_client, "cool_auth", "carol")
    await repo.upsert_state("cool_auth", {"cb_status": CbStatus.COOLING.value,
                                          "failure_category": ErrorCategory.AUTH.value,
                                          "cooldown_until": utc_now() + timedelta(minutes=5)})
    n = await reset_for_owner("carol", db=db_client)
    assert n == 1
    assert (await repo.get("cool_auth")).cb_status == CbStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_reset_for_owner_scoped_to_the_tested_provider(db_client):
    """POST /{provider_id}/test success resumes only agents whose agent slot
    runs on THAT provider: a per-agent agent_slots override wins, else the
    owner's user_slots default; an agent bound elsewhere stays paused."""
    repo = AgentCircuitBreakerRepository(db_client)
    for aid in ("scope_default", "scope_override", "scope_other"):
        await _seed_agent(db_client, aid, "fran")
        await repo.upsert_state(aid, _paused_row(cooldown_until=utc_now() + timedelta(minutes=5)))
    await db_client.insert("user_slots", {"user_id": "fran", "slot_name": "agent",
                                          "provider_id": "prov_a", "model": "m"})
    await db_client.insert("agent_slots", {"agent_id": "scope_override", "slot_name": "agent",
                                           "provider_id": "prov_b", "model": "m"})
    await db_client.insert("agent_slots", {"agent_id": "scope_other", "slot_name": "agent",
                                           "provider_id": "prov_c", "model": "m"})

    assert await reset_for_owner("fran", db=db_client, provider_id="prov_b") == 1
    assert (await repo.get("scope_override")).cb_status == CbStatus.ACTIVE.value
    assert (await repo.get("scope_default")).cb_status == CbStatus.PAUSED.value
    assert (await repo.get("scope_other")).cb_status == CbStatus.PAUSED.value

    assert await reset_for_owner("fran", db=db_client, provider_id="prov_a") == 1
    assert (await repo.get("scope_default")).cb_status == CbStatus.ACTIVE.value
    assert (await repo.get("scope_other")).cb_status == CbStatus.PAUSED.value

    # No provider → the reconfigure semantics: everything owned.
    assert await reset_for_owner("fran", db=db_client) == 1
    assert (await repo.get("scope_other")).cb_status == CbStatus.ACTIVE.value


def test_exhausted_wallet_is_quota_not_business():
    """Free-tier exhaustion lost its dedicated exception type when the wallet
    moved onto the gateway: it now arrives as a plain 429 whose only signal is
    the body. A type-only rule demoted it to BUSINESS — platform-alert, never a
    pause — i.e. an exhausted user retrying forever."""
    budget = (
        "litellm.BudgetExceededError: Budget has been exceeded! "
        "Key=free::u1 Current cost: 10.02, Max budget: 10.0"
    )
    assert classify_agent_error("unknown", budget) == ErrorCategory.QUOTA
    # Even when the SDK surfaces the 429 as a rate-limit type, the body wins.
    assert classify_agent_error("RateLimitError", budget) == ErrorCategory.QUOTA


def test_a_genuine_rate_limit_is_still_transient():
    """The guard on the fix above: consulting balance markers must not swallow
    real 429s, or a throttled provider would pause the agent instead of cooling."""
    assert classify_agent_error(
        "RateLimitError", "Error code: 429 - rate limit exceeded"
    ) == ErrorCategory.TRANSIENT
    assert classify_agent_error(
        "unknown", "429 Too Many Requests"
    ) == ErrorCategory.TRANSIENT


# ── 2026-09-09: peek_skip, the read-only twin of should_skip ────────────────


@pytest.mark.asyncio
async def test_peek_skip_reads_every_non_active_status_as_held(db_client):
    from datetime import timedelta

    from narranexus.platform.agent_framework.loop.circuit_breaker import peek_skip
    from narranexus.platform.utils.timezone import utc_now

    async def _row(agent_id, **cols):
        await db_client.insert(
            "instance_agent_circuit_breaker", {"agent_id": agent_id, **cols}
        )

    assert await peek_skip("nobody", db=db_client) == (False, None)
    await _row("act", cb_status="active")
    assert await peek_skip("act", db=db_client) == (False, None)
    await _row("pau", cb_status="paused", paused_reason="quota")
    assert await peek_skip("pau", db=db_client) == (True, "paused:quota")
    await _row("cool", cb_status="cooling", cooldown_until=utc_now() + timedelta(minutes=5))
    assert await peek_skip("cool", db=db_client) == (True, "cooling")
    await _row("cooled", cb_status="cooling", cooldown_until=utc_now() - timedelta(minutes=5))
    assert await peek_skip("cooled", db=db_client) == (False, None)
    await _row("fut", cb_status="probing")
    assert await peek_skip("fut", db=db_client) == (True, "probing")
    # Read-only: no row changed.
    rows = await db_client.get("instance_agent_circuit_breaker", {})
    assert sorted(r["cb_status"] for r in rows) == ["active", "cooling", "cooling", "paused", "probing"]


@pytest.mark.asyncio
async def test_peek_skip_fails_open():
    from narranexus.platform.agent_framework.loop.circuit_breaker import peek_skip

    class _Dead:
        async def get_one(self, *_a, **_k):
            raise RuntimeError("db down")

    assert await peek_skip("x", db=_Dead()) == (False, None)


def test_a_forbidden_only_message_still_classifies_as_auth_for_the_breaker():
    """#389 I3: "forbidden" moved out of the strict `is_credential_error` into
    the loose `is_auth_like_error`; the breaker must keep using the loose one
    or an owner-actionable 403 with no status digits files as BUSINESS."""
    assert classify_agent_error("X", "request forbidden by upstream policy") == ErrorCategory.AUTH


# Every production file that constructs a turn, as (gated, ungated, why):
# how many of its construction points pass a breaker gate, how many are
# deliberately ungated, and the reason for the ungated ones (None when there
# are none). Counted with the regex in the test below on 2026-09-11 (#394
# fourth review M-A, fifth review M-5, sixth review M-2). One table, because
# a file can hold both kinds: keyed by file alone, a gated point and an
# exempt one in the same file could only be registered as "gated" with a
# comment, and the gate check degraded to "a gate exists somewhere in the
# file". A changed count fails until the author confirms which kind the new
# point is and updates the numbers here.
_TURN_CONSTRUCTORS = {
    "backend/routes/openai_compat.py": (1, 0, None),
    "backend/routes/websocket.py": (1, 0, None),
    "plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/lark_trigger.py": (1, 0, None),
    "plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/matrix_trigger.py": (1, 0, None),
    # A2A tasks/send and tasks/sendSubscribe.
    "plugins/builtin.chat/src/narranexus_plugins/chat_module/chat_trigger.py": (2, 0, None),
    # _run_agent_turn (admit_turn) is gated. The silent memory batch is not:
    # the breaker guards turns that spend the agent-slot credential, and
    # silent=True runs SilentAct, which starts none (zero agent LLM calls).
    # It has no retry queue, so skipping it in any state loses the room's
    # messages from memory for good. Its memory/narrative calls use the
    # helper slot, which in the default one-key setup is the SAME credential;
    # helper health is the helper side's concern, not this breaker's
    # (#394 fifth review N-1, sixth review I-2).
    "src/narranexus/platform/channel/channel_trigger_base.py": (
        1, 1, "silent memory batch: no agent-slot turn, no retry queue"),
    "src/narranexus/platform/message_bus/message_bus_trigger.py": (1, 0, None),
    "src/narranexus/platform/services/module_poller.py": (1, 0, None),
    # The runtime seams themselves: they settle a probe their caller won,
    # they never decide whether a turn may start.
    "src/narranexus/platform/agent_runtime/client.py": (0, 4, "runtime client seam"),
    "src/narranexus/platform/agent_runtime/background_run.py": (
        0, 1, "run object built after the WS/openai gate"),
    "src/narranexus/platform/agent_runtime/agent_runtime.py": (
        0, 2, "the runtime (plus its dev-only demo function)"),
    # Jobs are scheduled work with their own consecutive-failure breaker
    # (job_trigger); the real-time breaker gates dialogue turns only.
    "plugins/builtin.job/src/narranexus_plugins/job_module/job_trigger.py": (0, 1, "own job breaker"),
    # Skill study is a one-shot run the owner starts from the Skills panel;
    # nothing re-triggers it, so there is no retry storm to hold off.
    "plugins/builtin.skills/src/narranexus_plugins/skill_module/routes.py": (
        0, 1, "owner-initiated one-shot"),
}


def test_every_turn_entry_passes_the_breaker_gate():
    """#394 second review I-3: the sweep is by TURN CONSTRUCTION POINT, not
    by existing gate — the first sweep missed channel_trigger_base (and the
    Lark / NarraMessenger / A2A entries) because it looked where gates
    already were. Every production file that starts a turn must be
    registered above with its gated / ungated split (and a reason for any
    ungated point); a file with a gated point must call a gate (the two
    steps, ``admit_turn``, the channel helper, or ``peek_skip`` for entries
    that cannot settle)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    construct = re.compile(r"run_and_collect\(|\.run_stream\(|BackgroundRun\(|AgentRuntime\(\)")
    gate = re.compile(r"\b(should_skip|try_begin_probe|admit_turn|peek_skip|_circuit_admission)\(")
    files = [*root.joinpath("src").rglob("*.py"), *root.joinpath("backend").rglob("*.py")]
    files += [p for p in root.joinpath("plugins").rglob("*.py") if "/src/" in p.as_posix()]
    found = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        count = len(construct.findall(text))
        if not count:
            continue
        rel = path.relative_to(root).as_posix()
        assert rel in _TURN_CONSTRUCTORS, f"{rel} starts a turn and is not registered"
        gated, _ungated, _why = _TURN_CONSTRUCTORS[rel]
        if gated:
            assert gate.search(text), f"{rel} starts a turn with no breaker gate"
        found[rel] = count
    # Every entry still constructs exactly the registered number of turns,
    # and every ungated point is explained.
    assert found == {rel: g + u for rel, (g, u, _why) in _TURN_CONSTRUCTORS.items()}
    for rel, (_g, ungated, why) in _TURN_CONSTRUCTORS.items():
        assert (ungated > 0) == (why is not None), rel
