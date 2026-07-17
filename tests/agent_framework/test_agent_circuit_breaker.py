"""
@file_name: test_agent_circuit_breaker.py
@author:
@date: 2026-07-13
@description: Unit/integration tests for the real-time-layer Agent
circuit-breaker service (classification + escalation split + skip-gate +
reset), against a real in-memory sqlite.
"""

from datetime import timedelta

import pytest

from xyz_agent_context.agent_framework import agent_circuit_breaker as cb
from xyz_agent_context.agent_framework.agent_circuit_breaker import (
    AUTH_QUOTA_PAUSE_THRESHOLD,
    classify_agent_error,
    record_failure,
    record_success,
    reset_agent,
    reset_for_owner,
    should_skip,
)
from xyz_agent_context.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from xyz_agent_context.schema import CbStatus, ErrorCategory, PausedReason
from xyz_agent_context.utils.timezone import utc_now


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
    for t in ("QuotaExceededError", "FreeTierExhaustedError",
              "NoProviderConfiguredError", "SystemDefaultUnavailable",
              "LLMConfigNotConfigured"):
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
        await record_failure(aid, "QuotaExceededError", "no quota", db=db_client)
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
    assert await should_skip(aid, db=db_client) == (False, None)


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
    assert await should_skip("ghost", db=db_client) == (False, None)

    # paused → skip
    await repo.upsert_state("p", {"cb_status": CbStatus.PAUSED.value,
                                  "paused_reason": PausedReason.AUTH.value})
    skip, reason = await should_skip("p", db=db_client)
    assert skip and reason.startswith("paused:auth")

    # cooling in the future → skip
    await repo.upsert_state("c_future", {
        "cb_status": CbStatus.COOLING.value,
        "cooldown_until": utc_now() + timedelta(minutes=5),
    })
    assert await should_skip("c_future", db=db_client) == (True, "cooling")

    # cooling already elapsed → allow (lazy expiry)
    await repo.upsert_state("c_past", {
        "cb_status": CbStatus.COOLING.value,
        "cooldown_until": utc_now() - timedelta(minutes=5),
    })
    assert await should_skip("c_past", db=db_client) == (False, None)


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
async def test_reset_for_owner_clears_authquota_cooling(db_client):
    repo = AgentCircuitBreakerRepository(db_client)
    await _seed_agent(db_client, "cool_auth", "carol")
    await repo.upsert_state("cool_auth", {"cb_status": CbStatus.COOLING.value,
                                          "failure_category": ErrorCategory.AUTH.value,
                                          "cooldown_until": utc_now() + timedelta(minutes=5)})
    n = await reset_for_owner("carol", db=db_client)
    assert n == 1
    assert (await repo.get("cool_auth")).cb_status == CbStatus.ACTIVE.value
