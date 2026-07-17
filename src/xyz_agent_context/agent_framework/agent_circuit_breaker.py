"""
@file_name: agent_circuit_breaker.py
@author:
@date: 2026-07-13
@description: Agent-level circuit-breaker for the REAL-TIME dialogue layer.

Problem it solves: when an Agent's real-time turns keep failing (dead
OAuth/API key → 401 every turn, exhausted balance, model unavailable), the
trigger entry points (WebSocket fresh run, message-bus poll, module poller)
keep re-triggering it forever, burning polling resources. The Job scheduler
already has a breaker; the real-time layer had none.

Design (see the plan for the full rationale):

  Every FAILED turn → increment a per-agent consecutive-failure counter and
  enter COOLING with exponential backoff. Escalation then SPLITS by cause:

    * auth / quota  — won't self-heal (needs a key/balance change). PAUSE
      after ``AUTH_QUOTA_PAUSE_THRESHOLD`` consecutive same-category failures
      and alert the owner. Recovers on key-reconfigure (reset_for_owner) or a
      manual reset.
    * transient / business — self-healing, or the user's chosen flaky model.
      NEVER hard-pauses (binding rule #15 forbids the platform giving up on a
      user's model). Cools with backoff (capped 1h) and retries forever; a
      diagnostic audit row is dropped after a longer streak so it isn't
      silent.

  A success resets everything. A category change resets the streak, so "3
  consecutive auth failures" can never be diluted by an unrelated blip.

Binding rules #14/#15: this only reacts to turns that ALREADY finished and
failed, it gates the SCHEDULING of new turns, and it NEVER cancels an
in-flight loop or caps loop length.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from loguru import logger

from xyz_agent_context.agent_framework.llm_failure import (
    classify_self_serviceable,
    is_credential_error,
    redact_secrets,
)
from xyz_agent_context.agent_runtime.response_processor import _is_auth_failure
from xyz_agent_context.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from xyz_agent_context.schema import (
    CbStatus,
    ErrorCategory,
    PAUSING_CATEGORIES,
)
from xyz_agent_context.services.background_llm_alerts import (
    alert_agent_paused,
    alert_agent_transient_streak,
    audit_agent_internal_streak,
)
from xyz_agent_context.utils.backoff import compute_cooldown_seconds
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.timezone import utc_now

# Consecutive same-category auth/quota failures before a hard PAUSE. Small on
# purpose: a dead key / exhausted balance is flagged in ~3 min (the backoff
# spans 60s + 120s before the 3rd strike) instead of burning ~2h to reach 8.
AUTH_QUOTA_PAUSE_THRESHOLD = 3

# Neither TRANSIENT nor BUSINESS ever pauses; after this many consecutive we
# raise an alert so a chronically-failing agent isn't invisible. For TRANSIENT
# (provider-side) that alert reaches the OWNER (their model/provider keeps
# failing); for BUSINESS (our bug / permanent) it stays INTERNAL (platform),
# never the owner — the owner can't act on our defect.
SUSTAINED_FAILURE_ALERT_THRESHOLD = 5

# Duplicated from job_trigger on purpose — the breaker must not import the Job
# module (modules are independent, binding rule #3). These are error TYPES that
# mean "quota/provider exhaustion that won't fix itself by waiting". This is
# also the extension point for a future "Executor batch balance insufficient".
_NO_QUOTA_ERROR_TYPES: frozenset[str] = frozenset({
    "QuotaExceededError",
    "FreeTierExhaustedError",
    "NoProviderConfiguredError",
    "SystemDefaultUnavailable",
    "LLMConfigNotConfigured",
})

# Provider-side transient error TYPES (self-healing or the user's flaky model).
# Positively identified so the residual "unknown / our-own-bug" bucket can be
# routed to BUSINESS (internal-only) instead of being surfaced to the owner.
_TRANSIENT_ERROR_TYPES: frozenset[str] = frozenset({
    "TimeoutError", "ReadTimeout", "ConnectTimeout", "APITimeoutError",
    "RateLimitError", "APIConnectionError", "APIError", "APIStatusError",
    "InternalServerError", "ServiceUnavailableError",
    "ConnectionError", "ConnectionResetError",
})

# Provider-side transient MESSAGE markers (network / 5xx / rate-limit / overload).
_TRANSIENT_MARKERS: tuple[str, ...] = (
    "timeout", "timed out", "rate limit", "rate-limit", "too many requests",
    "overloaded", "server is busy", "temporarily unavailable",
    "service unavailable", "bad gateway", "gateway timeout",
    "connection reset", "connection error", "connection aborted",
    "429", "502", "503", "504",
)


def _looks_transient(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _TRANSIENT_MARKERS)


def classify_agent_error(
    error_type: Optional[str], error_message: Optional[str]
) -> ErrorCategory:
    """Classify a failed turn's cause.

    Order matters and is deliberate:
      1. QUOTA — exact error TYPE match (precise; avoids quota-vs-ratelimit
         substring traps).
      2. TRANSIENT — positively identified provider-side signatures (network /
         5xx / rate-limit / overload). Checked BEFORE auth so a message like
         "provider temporarily unavailable" isn't mis-swept into AUTH by the
         broad "provider" credential marker.
      3. AUTH — dead credentials (401/403, invalid/expired key, re-login).
      4. BUSINESS — everything else: our-own pipeline bug, a permanent client
         error (context too long, unknown model, content policy), or simply an
         error we can't confidently attribute. This is the real residual bucket;
         it never pauses and, on a sustained streak, alerts the PLATFORM only —
         never the owner, who can't act on our defect.
    """
    et = error_type or ""
    msg = error_message or ""
    if et in _NO_QUOTA_ERROR_TYPES:
        return ErrorCategory.QUOTA
    if et in _TRANSIENT_ERROR_TYPES or _looks_transient(msg):
        return ErrorCategory.TRANSIENT
    if (
        _is_auth_failure(et, msg)
        or is_credential_error(et)
        or is_credential_error(msg)
        # `is_credential_error`'s " 403"/"(403" markers need a delimiter before
        # the digits, so a string-leading "403 Forbidden" slips through. Catch
        # the unambiguous word so a permission/credential 403 is treated as
        # AUTH (owner-actionable), not misrouted to BUSINESS (platform-only).
        or "forbidden" in msg.lower()
    ):
        return ErrorCategory.AUTH
    return ErrorCategory.BUSINESS


def _as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Coerce a possibly-naive datetime (sqlite round-trips as naive) to an
    aware UTC datetime for safe comparison against ``utc_now()``."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _resolve_owner(db, agent_id: str) -> Optional[str]:
    """Look up the agent owner (agents.created_by). Best-effort — a missing
    owner just means no inbox notice, not a failure."""
    try:
        row = await db.get_one("agents", {"agent_id": agent_id})
        return (row or {}).get("created_by") or None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[agent-cb] owner lookup failed for {agent_id}: {e}")
        return None


async def record_failure(
    agent_id: str,
    error_type: Optional[str],
    error_message: Optional[str],
    db=None,
) -> None:
    """Record one FAILED real-time turn and advance the breaker state.

    Callers MUST treat this as best-effort (wrap in try/except) — a breaker
    write must never break turn finalization.
    """
    # Deterministic, user-self-serviceable failures (context window too small,
    # no credits, bad model id) must NOT advance the breaker. They don't heal
    # by waiting, so a cooldown would only block the CORRECTED retry after the
    # user switches models — punishing them for doing exactly what the
    # actionable error told them (binding rule #14/#15: never be the
    # interruption source). They're also not a provider-hammering risk (the
    # provider rejects them instantly). The turn already surfaced an actionable
    # error; the breaker stays out entirely — no cool, no pause, no counter
    # change (an unrelated prior streak is left intact).
    if classify_self_serviceable(error_type, error_message) is not None:
        logger.debug(
            f"[agent-cb] agent {agent_id} self-serviceable failure "
            f"({error_type}) — breaker not advanced"
        )
        return

    db = db or await get_db_client()
    repo = AgentCircuitBreakerRepository(db)
    category = classify_agent_error(error_type, error_message)

    row = await repo.get(agent_id)
    prev_count = row.consecutive_failure_count if row else 0
    prev_category = row.failure_category if row else None  # stored as str value

    # Same-category streak: a category change resets the counter so an
    # unrelated blip can't dilute an auth/quota streak toward its threshold.
    count = prev_count + 1 if prev_category == category.value else 1

    now = utc_now()
    cooldown_secs = compute_cooldown_seconds(count)
    updates: dict = {
        "consecutive_failure_count": count,
        "failure_category": category.value,
        "last_error": redact_secrets(error_message),
        "cooldown_until": now + timedelta(seconds=cooldown_secs),
    }

    is_pause = category in PAUSING_CATEGORIES and count >= AUTH_QUOTA_PAUSE_THRESHOLD
    if is_pause:
        updates["cb_status"] = CbStatus.PAUSED.value
        updates["paused_reason"] = category.value  # auth | quota
        updates["paused_at"] = now
    else:
        updates["cb_status"] = CbStatus.COOLING.value
        updates["paused_reason"] = None
        updates["paused_at"] = None

    await repo.upsert_state(agent_id, updates)

    if is_pause:
        owner = await _resolve_owner(db, agent_id)
        await alert_agent_paused(
            agent_id=agent_id,
            reason=category.value,
            error=error_message,
            owner_user_id=owner,
        )
        logger.warning(
            f"[agent-cb] agent {agent_id} PAUSED after {count} consecutive "
            f"{category.value} failures"
        )
    elif count == SUSTAINED_FAILURE_ALERT_THRESHOLD:
        # Sustained non-pausing streak (fires once per streak — resets on any
        # success). Route by who can act on it:
        if category == ErrorCategory.TRANSIENT:
            # Provider/model side (the user's choice) — tell the OWNER, factual
            # and non-prescriptive (never "switch your model", per rule #15).
            owner = await _resolve_owner(db, agent_id)
            await alert_agent_transient_streak(
                agent_id=agent_id,
                owner_user_id=owner,
                consecutive_failures=count,
                error=error_message,
            )
        else:
            # BUSINESS: our-own bug / permanent client error / unattributable.
            # The owner can't act on it → PLATFORM-only (internal audit + log),
            # never an owner notice.
            await audit_agent_internal_streak(
                agent_id=agent_id, consecutive_failures=count, error=error_message
            )


async def record_success(agent_id: str, db=None) -> None:
    """Record a successful turn — clears any failure streak / pause.

    Best-effort at the call site. A no-op when the agent is already clean.
    """
    db = db or await get_db_client()
    repo = AgentCircuitBreakerRepository(db)
    row = await repo.get(agent_id)
    if row is None:
        return
    if row.cb_status == CbStatus.ACTIVE.value and row.consecutive_failure_count == 0:
        return  # already clean — skip a pointless write
    await repo.upsert_state(agent_id, _CLEAN_STATE)


async def should_skip(agent_id: str, db=None) -> Tuple[bool, Optional[str]]:
    """Should the given agent's next real-time turn be skipped?

    Returns ``(skip, reason)``. FAIL-OPEN: any read error returns
    ``(False, None)`` — a breaker glitch must never block a healthy turn.

    Lazy expiry: a COOLING row whose ``cooldown_until`` has elapsed is
    ALLOWED through; the retry then either succeeds (→ reset) or fails
    (→ re-backoff). A PAUSED row never expires by time — only by reset.
    """
    try:
        db = db or await get_db_client()
        repo = AgentCircuitBreakerRepository(db)
        row = await repo.get(agent_id)
        if row is None:
            return (False, None)
        if row.cb_status == CbStatus.PAUSED.value:
            return (True, f"paused:{row.paused_reason or 'unknown'}")
        if row.cb_status == CbStatus.COOLING.value:
            until = _as_aware_utc(row.cooldown_until)
            if until is not None and until > utc_now():
                return (True, "cooling")
        return (False, None)
    except Exception as e:  # noqa: BLE001 — fail open, never block a turn
        logger.warning(f"[agent-cb] should_skip read failed for {agent_id}: {e}")
        return (False, None)


async def reset_agent(agent_id: str, db=None) -> None:
    """Manually clear an agent's breaker state back to ACTIVE (idempotent)."""
    db = db or await get_db_client()
    repo = AgentCircuitBreakerRepository(db)
    row = await repo.get(agent_id)
    if row is None:
        return
    await repo.upsert_state(agent_id, _CLEAN_STATE)
    logger.info(f"[agent-cb] agent {agent_id} circuit-breaker reset to active")


async def reset_for_owner(user_id: str, db=None) -> int:
    """Auto-resume the owner's auth/quota-blocked agents after a key/balance
    reconfigure. Clears PAUSED (all pauses are auth/quota) and in-progress
    auth/quota COOLING streaks; a transient COOLING streak is left alone
    (unrelated to the key). Returns the number of agents reset. Best-effort.
    """
    db = db or await get_db_client()
    repo = AgentCircuitBreakerRepository(db)
    try:
        candidates = (
            await repo.find_by_status(CbStatus.PAUSED.value)
            + await repo.find_by_status(CbStatus.COOLING.value)
        )
        if not candidates:
            return 0
        owned = await _owner_agent_ids(db, user_id)
        reset = 0
        for cb in candidates:
            if cb.agent_id not in owned:
                continue
            # PAUSED is always auth/quota; for COOLING only clear auth/quota
            # streaks (a transient cooldown is unrelated to the key).
            if cb.cb_status == CbStatus.COOLING.value and cb.failure_category not in (
                ErrorCategory.AUTH.value,
                ErrorCategory.QUOTA.value,
            ):
                continue
            await repo.upsert_state(cb.agent_id, _CLEAN_STATE)
            reset += 1
        if reset:
            logger.info(
                f"[agent-cb] reset {reset} agent(s) for owner {user_id} "
                f"after provider reconfigure"
            )
        return reset
    except Exception as e:  # noqa: BLE001 — best-effort auto-resume
        logger.warning(f"[agent-cb] reset_for_owner({user_id}) failed: {e}")
        return 0


async def _owner_agent_ids(db, user_id: str) -> set[str]:
    """agent_id set owned by a user (agents.created_by)."""
    rows = await db.get("agents", filters={"created_by": user_id})
    return {r["agent_id"] for r in rows if r and r.get("agent_id")}


# Canonical "healthy / no streak" write, shared by success + reset paths.
_CLEAN_STATE: dict = {
    "cb_status": CbStatus.ACTIVE.value,
    "consecutive_failure_count": 0,
    "failure_category": None,
    "cooldown_until": None,
    "paused_reason": None,
    "paused_at": None,
}
