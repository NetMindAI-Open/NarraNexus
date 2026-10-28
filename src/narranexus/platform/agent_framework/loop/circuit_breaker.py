"""
@file_name: circuit_breaker.py
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
      and alert the owner. Recovers on key-reconfigure (reset_for_owner), a
      manual reset, OR a half-open probe once the pause's own timeout
      elapses (GitHub #117 — PAUSED is not a dead end; see ``should_skip``).
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

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Tuple

from loguru import logger

from narranexus.platform.agent_framework.llm.failure import (
    classify_self_serviceable,
    is_auth_like_error,
    redact_secrets,
)
from narranexus.platform.agent_framework.providers.model_identity import (
    resolve_agent_config_slot,
)
from narranexus.platform.agent_runtime.response_processor import _is_auth_failure
from narranexus.platform.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from narranexus.platform.schema import (
    AgentCircuitBreaker,
    CbStatus,
    ErrorCategory,
    PAUSING_CATEGORIES,
    EXECUTOR_INFRA_ERROR_TYPE,
    OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE,
)
from narranexus.platform.services.background_llm_alerts import (
    alert_agent_paused,
    alert_agent_transient_streak,
    audit_agent_internal_streak,
)
from narranexus.platform.utils.backoff import compute_cooldown_seconds
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.run_liveness import STATE_RUNNING, run_is_live
from narranexus.platform.utils.timezone import coerce_utc, utc_now

# Consecutive same-category auth/quota failures before a hard PAUSE. Small on
# purpose: a dead key / exhausted balance is flagged in ~3 min (the backoff
# spans 60s + 120s before the 3rd strike) instead of burning ~2h to reach 8.
AUTH_QUOTA_PAUSE_THRESHOLD = 3

# Half-open: once PAUSED, a single probe turn is allowed through after this
# many seconds — self-heal without a manual reset or a reconfigure (GitHub
# #117: PAUSED used to be a dead end). Grows with consecutive same-category
# trips (a re-pause after a failed probe doubles it) so a chronically-dead
# credential isn't re-probed every 5 minutes forever; capped at
# PAUSE_HALF_OPEN_CAP_SECONDS.
PAUSE_HALF_OPEN_BASE_SECONDS = 300  # 5 minutes
PAUSE_HALF_OPEN_CAP_SECONDS = 6 * 3600  # 6 hours
# Doublings after which the cap is guaranteed to win: ceil(log2(cap / base)).
_HALF_OPEN_MAX_DOUBLINGS = (PAUSE_HALF_OPEN_CAP_SECONDS // PAUSE_HALF_OPEN_BASE_SECONDS).bit_length()

# How long a claimed probe grant is honored on the WALL CLOCK before the row
# is even considered for re-claim. This is NOT a turn-length ceiling (binding
# rule #14 forbids one): once the claiming turn's run row exists it binds its
# ``event_id`` to the claim (``bind_probe_run``), and from then on the claim
# lives exactly as long as THAT run is alive (``_claimant_may_be_live`` —
# heartbeat-fresh ``events`` row, the same ``run_is_live`` rule
# ``run_recorder.sweep_stale_runs`` uses). A probe turn that runs for hours
# keeps its grant for hours. The timer therefore only bounds the window
# between the claim and the run row (or a claimant that died inside it),
# which is seconds; 5 minutes is generous for THAT, not for a turn.
PROBE_GRANT_SECONDS = 300

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
    "NoProviderConfiguredError",
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


def _is_out_of_credit(error_type: str, message: str) -> bool:
    """True when the failure is 'this credential has no money left'.

    Shares the single classifier in ``llm/failure`` rather than keeping a
    fourth marker list — that module already had to learn the gateway's
    budget wording so background jobs pause, and two lists would drift.
    """
    from narranexus.platform.agent_framework.llm.failure import (
        OUT_OF_CREDIT_REASONS,
        classify_self_serviceable,
    )

    # Membership, not equality: free-tier exhaustion is a SECOND out-of-credit
    # reason (2026-07-30). Comparing against one member demoted it to BUSINESS —
    # platform-alert, never a pause — i.e. an exhausted user retrying forever.
    return classify_self_serviceable(error_type, message) in OUT_OF_CREDIT_REASONS


def classify_agent_error(
    error_type: Optional[str], error_message: Optional[str]
) -> ErrorCategory:
    """Classify a failed turn's cause.

    Order matters and is deliberate:
      1. QUOTA — exact error TYPE match, PLUS the narrow insufficient-balance
         message markers. The type check alone was enough while free-tier
         exhaustion had its own exception class; since the wallet moved onto
         the gateway (2026-07-28) exhaustion arrives as a plain HTTP 429 whose
         only signal is the body ("Budget has been exceeded!"), so a
         type-only rule silently demoted it to BUSINESS — platform-alert,
         never a pause, i.e. an exhausted user retrying forever. The markers
         consulted here are the balance ones only ("insufficient balance",
         "budget has been exceeded", ...), never "429"/"rate limit", so the
         quota-vs-ratelimit trap this ordering was built to avoid stays shut —
         and checking them BEFORE the transient rule is what keeps a budget
         429 out of the rate-limit bucket.
      2. TRANSIENT — positively identified provider-side signatures (network /
         5xx / rate-limit / overload). Checked BEFORE auth so a transient that
         happens to mention a credential word ("authentication service
         timed out") lands here and not in AUTH.
      3. AUTH — dead credentials (401/403, invalid/expired key, re-login).
      4. BUSINESS — everything else: our-own pipeline bug, a permanent client
         error (context too long, unknown model, content policy), or simply an
         error we can't confidently attribute. This is the real residual bucket;
         it never pauses and, on a sustained streak, alerts the PLATFORM only —
         never the owner, who can't act on our defect.
    """
    et = error_type or ""
    msg = error_message or ""
    if et in _NO_QUOTA_ERROR_TYPES or _is_out_of_credit(et, msg):
        return ErrorCategory.QUOTA
    if et in _TRANSIENT_ERROR_TYPES or _looks_transient(msg):
        return ErrorCategory.TRANSIENT
    if (
        _is_auth_failure(et, msg)
        # The loose predicate: "forbidden" counts here, where the cost of a
        # miss is an owner-actionable 403 filed as platform-only. It stays out
        # of the strict `is_credential_error` used by control flow (#389 I3).
        or is_auth_like_error(et)
        or is_auth_like_error(msg)
    ):
        return ErrorCategory.AUTH
    return ErrorCategory.BUSINESS


def _compute_half_open_delay_seconds(consecutive_failure_count: int) -> int:
    """Delay before a PAUSED breaker allows one half-open probe through.

    ``consecutive_failure_count`` already only advances on same-category
    failures (a category change resets it, see ``record_failure``), and it
    keeps incrementing across repeated pause->probe->fail cycles (a failed
    probe IS another same-category failure). So each extra trip past the
    pause threshold doubles the delay — first pause: base delay; the probe
    fails and re-pauses: 2x; fails again: 4x; ... capped at
    PAUSE_HALF_OPEN_CAP_SECONDS. This reuses the same signal
    ``compute_cooldown_seconds`` uses for COOLING, but with its own base/cap
    — the two schedules serve different purposes (COOLING retries a
    self-healing failure fast; PAUSED gates a probe against a credential
    that has already proven dead 3 times).
    """
    trips_over_threshold = max(0, consecutive_failure_count - AUTH_QUOTA_PAUSE_THRESHOLD)
    # Clamp the EXPONENT before raising: past the point where the cap wins,
    # 2**n would only build an ever-larger int for min() to discard.
    trips_over_threshold = min(trips_over_threshold, _HALF_OPEN_MAX_DOUBLINGS)
    return min(
        PAUSE_HALF_OPEN_BASE_SECONDS * (2 ** trips_over_threshold),
        PAUSE_HALF_OPEN_CAP_SECONDS,
    )


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


def _is_self_serviceable(error_type: Optional[str], error_message: Optional[str]) -> bool:
    # Deterministic, user-self-serviceable failures (context window too small,
    # no credits, bad model id) must NOT advance the breaker. They don't heal
    # by waiting, so a cooldown would only block the CORRECTED retry after the
    # user switches models — punishing them for doing exactly what the
    # actionable error told them (binding rule #14/#15: never be the
    # interruption source). They're also not a provider-hammering risk (the
    # provider rejects them instantly). The turn already surfaced an
    # actionable error.
    return classify_self_serviceable(error_type, error_message) is not None


def _is_executor_infra(error_type: Optional[str], error_message: Optional[str]) -> bool:
    # Executor-infra failures (OOM / unreachable) are a PLATFORM fault, not the
    # agent's — and the surfaced ``infra_transient`` error tells the user to
    # "resend shortly". Advancing the breaker would COOL the agent for 60s+ and
    # reject that very resend (websocket should_skip), turning one platform
    # blip into a self-inflicted second punishment. Same reasoning as the
    # self-serviceable exemption (binding rule #15: never be the interruption
    # source).
    return error_type == EXECUTOR_INFRA_ERROR_TYPE


def _is_output_budget_exhausted(
    error_type: Optional[str], error_message: Optional[str]
) -> bool:
    # Output-budget exhaustion (a thinking model spent its whole max_tokens on
    # reasoning, even after the framework's one budget-doubling replay) is our
    # own budget choice meeting the model the user picked. It is deterministic
    # — waiting never heals it — so a cooldown would only reject the user's
    # next message: the platform as the interruption source (binding rule
    # #15). Keyed on the structured error_type ONLY: the message can echo
    # provider- and user-controlled text, and a phrase match there would let
    # caller content switch the breaker off for every failure class.
    return error_type == OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE


# Failures that must not touch the breaker AT ALL — no cool, no pause, no
# counter change (an unrelated prior streak is left intact). Checked in order;
# the first match names the exemption in the debug log. Order is deliberate:
# self-serviceable first (its classifier also reads the message), then the
# two exact error_type markers.
_BREAKER_EXEMPTIONS: tuple[
    tuple[str, Callable[[Optional[str], Optional[str]], bool]], ...
] = (
    ("self-serviceable", _is_self_serviceable),
    ("executor-infra (platform-side)", _is_executor_infra),
    ("output-budget exhaustion", _is_output_budget_exhausted),
)


def breaker_exemption(
    error_type: Optional[str], error_message: Optional[str]
) -> Optional[str]:
    """Name of the exemption that keeps this failure out of the breaker, or
    ``None`` when the failure must advance it."""
    for name, applies in _BREAKER_EXEMPTIONS:
        if applies(error_type, error_message):
            return name
    return None


@dataclass(frozen=True)
class GateVerdict:
    """``should_skip``'s answer, plus the row it read.

    ``row`` / ``row_known`` let the same entry point hand the read straight to
    ``try_begin_probe(prior=...)`` instead of reading the row a second time on
    every turn (#394 review M1). Reusing a slightly old read is safe: the
    probe claim is a ``probe_token`` compare-and-swap, so a stale PAUSED row
    can only lose the claim, never double-win it; an ACTIVE row gates nothing
    either way. ``row_known`` is False when the read failed (fail-open) — the
    claim then re-reads.
    """

    skip: bool
    reason: Optional[str]
    row: Optional[AgentCircuitBreaker] = None
    row_known: bool = False


@dataclass(frozen=True)
class TurnAdmission:
    """``try_begin_probe``'s answer.

    ``probe_token`` is set ONLY when this turn won the half-open probe. The
    turn must carry it to its settlement — ``record_success`` /
    ``record_failure`` / ``settle_probe`` / ``release_probe`` — because the
    token is the claim's identity: nothing without it may settle the probe.

    ``window`` is set on a refusal: the breaker window (``cb_status`` +
    ``cooldown_until``) of the same row read that produced ``reason``, so a
    channel can throttle its refusal notice per window without reading the
    row a second time (#394 fifth review M-2).
    """

    allowed: bool
    reason: Optional[str]
    probe_token: Optional[str] = None
    window: Optional[str] = None


def _window_of(row: Optional[AgentCircuitBreaker]) -> Optional[str]:
    """A refusal's breaker window key: a new pause, or a backoff doubled by
    a failed probe, is a new window."""
    if row is None:
        return None
    return f"{row.cb_status}|{row.cooldown_until}"


def _holds_probe(row: Optional[AgentCircuitBreaker], probe_token: Optional[str]) -> bool:
    """Whether ``probe_token`` is the live claim on ``row``."""
    return (
        probe_token is not None
        and row is not None
        and row.cb_status == CbStatus.PROBING.value
        and row.probe_token == probe_token
    )


async def record_failure(
    agent_id: str,
    error_type: Optional[str],
    error_message: Optional[str],
    db=None,
    *,
    probe_token: Optional[str] = None,
) -> None:
    """Record one FAILED real-time turn and advance the breaker state.

    Callers MUST treat this as best-effort (wrap in try/except) — a breaker
    write must never break turn finalization.

    ``probe_token`` is the claim this turn won in ``try_begin_probe`` (None
    for an ordinary turn). The probe's outcome is decided ONLY by the turn
    holding the live claim (#394 review I1/I5):

      * row PROBING and this turn holds the token → the half-open probe's
        OUTCOME, settled by probe semantics (below), written by a token CAS.
      * row PROBING and this turn does NOT hold it (an unrelated long run
        that started before the claim, a turn from another entry) → the
        breaker is left untouched; the claimant will settle.
      * any other status → the ordinary streak rules.

    Probe semantics (GitHub #117 review):

      * auth/quota → the credential is still dead: re-PAUSE, streak +1, so
        the half-open delay doubles (``_compute_half_open_delay_seconds``).
      * anything else (transient blip, our own bug) → the probe proved
        NOTHING about the credential: stay PAUSED with the streak, category
        and paused_reason UNCHANGED and the SAME half-open delay re-armed.
        The streak is NOT advanced on purpose (binding rule #15): a blip must
        not push the owner's delay toward the 6h cap.
      * the ``_BREAKER_EXEMPTIONS`` classes (self-serviceable, executor-infra,
        output-budget exhaustion) leave the breaker untouched, but a held
        probe must still be settled or it would hang until the grant
        expires — it is released without a verdict.

    The exemptions are decided BEFORE any read, so an exempt failure of an
    ordinary turn (the common case) costs no database round-trip; only a
    probe holder reads, to settle (#394 review M2).
    """
    # Exempt classes (``_BREAKER_EXEMPTIONS``: self-serviceable, executor-
    # infra, output-budget exhaustion) never advance the breaker — no cool,
    # no pause, no counter change. A held probe is released without verdict.
    exempt_why = breaker_exemption(error_type, error_message)
    if exempt_why is not None:
        logger.debug(
            f"[agent-cb] agent {agent_id} {exempt_why} failure ({error_type}) — "
            f"breaker not advanced"
        )
        if probe_token is not None:
            await release_probe(agent_id, probe_token, db=db)
        return

    db = db or await get_db_client()
    repo = AgentCircuitBreakerRepository(db)
    row = await repo.get(agent_id)
    holds = _holds_probe(row, probe_token)
    if row is not None and row.cb_status == CbStatus.PROBING.value and not holds:
        logger.debug(
            f"[agent-cb] agent {agent_id} failure from a turn that does not "
            f"hold the half-open probe — left for the claimant to settle"
        )
        return

    category = classify_agent_error(error_type, error_message)
    prev_count = row.consecutive_failure_count if row else 0
    prev_category = row.failure_category if row else None  # stored as str value

    if holds and category not in PAUSING_CATEGORIES:
        await _rearm_pause_without_verdict(
            repo, row, probe_token, f"{category.value} failure", last_error=error_message
        )
        return

    if holds:
        # The probe failed for the reason it was paused: continue THAT streak.
        # The original category/reason is kept even if this turn classified
        # as the other pausing category (auth vs quota) — the pause is one
        # escalation ladder, not two.
        count = prev_count + 1
        if prev_category in (ErrorCategory.AUTH.value, ErrorCategory.QUOTA.value):
            category = ErrorCategory(prev_category)
    else:
        # Same-category streak: a category change resets the counter so an
        # unrelated blip can't dilute an auth/quota streak toward its
        # threshold.
        count = prev_count + 1 if prev_category == category.value else 1

    now = utc_now()
    is_pause = category in PAUSING_CATEGORIES and count >= AUTH_QUOTA_PAUSE_THRESHOLD
    if is_pause:
        updates: dict = {
            "consecutive_failure_count": count,
            "failure_category": category.value,
            "last_error": redact_secrets(error_message),
            "cb_status": CbStatus.PAUSED.value,
            "paused_reason": category.value,  # auth | quota
            "paused_at": now,
            # The half-open gate — NOT the COOLING backoff (only computed in
            # the else branch). should_skip / try_begin_probe read this same
            # field to decide when a probe may pass.
            "cooldown_until": now + timedelta(
                seconds=_compute_half_open_delay_seconds(count)
            ),
            **_NO_CLAIM,
        }
    else:
        updates = {
            "consecutive_failure_count": count,
            "failure_category": category.value,
            "last_error": redact_secrets(error_message),
            "cooldown_until": now + timedelta(seconds=compute_cooldown_seconds(count)),
            "cb_status": CbStatus.COOLING.value,
            "paused_reason": None,
            "paused_at": None,
            **_NO_CLAIM,
        }

    if holds:
        # Token CAS: if the owner reset the row (or it was otherwise settled)
        # since the read above, this verdict is stale and must not land.
        if not await repo.settle_probe(agent_id, probe_token, updates):
            logger.info(
                f"[agent-cb] agent {agent_id} probe claim no longer live; "
                f"failed probe outcome dropped"
            )
            return
    else:
        await repo.upsert_state(agent_id, updates)

    if is_pause:
        if holds:
            # The owner was alerted when the pause first tripped; a failed
            # probe is that same outage continuing, not a new event — one
            # log line, no repeat alert every 5min..6h.
            logger.warning(
                f"[agent-cb] agent {agent_id} half-open probe FAILED "
                f"({category.value}); re-PAUSED, streak={count}, next probe "
                f"in {_compute_half_open_delay_seconds(count)}s"
            )
            return
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


async def _rearm_pause_without_verdict(
    repo: AgentCircuitBreakerRepository,
    row: AgentCircuitBreaker,
    probe_token: str,
    why: str,
    *,
    last_error: Optional[str] = None,
) -> bool:
    """A held probe that ended WITHOUT a verdict on the credential (non-auth
    failure, exempt failure, user cancel, lost run) goes back to PAUSED
    exactly as it was — same streak, category and paused_reason — with the
    same half-open delay re-armed from now. Nothing is learned, so nothing
    escalates and nobody is re-alerted. Written by the ``probe_token`` CAS;
    returns False (and writes nothing) when that claim is no longer live."""
    count = row.consecutive_failure_count
    updates: dict = {
        "cb_status": CbStatus.PAUSED.value,
        "cooldown_until": utc_now()
        + timedelta(seconds=_compute_half_open_delay_seconds(count)),
        **_NO_CLAIM,
    }
    if last_error is not None:
        updates["last_error"] = redact_secrets(last_error)
    if not await repo.settle_probe(row.agent_id, probe_token, updates):
        return False
    logger.info(
        f"[agent-cb] agent {row.agent_id} half-open probe ended without a "
        f"verdict ({why}); re-PAUSED with the same delay "
        f"({_compute_half_open_delay_seconds(count)}s), streak={count}"
    )
    return True


async def release_probe(agent_id: str, probe_token: Optional[str], db=None) -> bool:
    """Hand back a half-open probe claim whose turn ends without a verdict —
    the user cancelled it (``background_run`` leaves CANCELLED turns out of
    the breaker), the entry point bailed out before the turn started, or the
    turn never reported. Back to PAUSED, same streak, same delay.

    Keyed on the claim's identity (#394 review I1): a no-op returning False
    unless the row is PROBING under exactly ``probe_token``. So it is safe —
    and idempotent — to call unconditionally at the end of every turn that
    was admitted: once the claim was settled (ACTIVE / re-PAUSED, token
    cleared), reset by the owner, or re-claimed by someone else, this writes
    nothing. It never touches a claim it does not hold, however many other
    runs the agent has alive. Best-effort by contract: never raises.
    """
    if probe_token is None:
        return False
    try:
        db = db or await get_db_client()
        repo = AgentCircuitBreakerRepository(db)
        row = await repo.get(agent_id)
        if not _holds_probe(row, probe_token):
            return False
        return await _rearm_pause_without_verdict(
            repo, row, probe_token, "probe turn never reported an outcome"
        )
    except Exception as e:  # noqa: BLE001 — observer never breaks the observed
        logger.warning(f"[agent-cb] release_probe({agent_id}) failed: {e}")
        return False


async def bind_probe_run(
    agent_id: str, probe_token: Optional[str], run_id: str, db=None
) -> bool:
    """Record which run is the claimant: the claiming turn calls this once
    its ``events`` row exists (``RunRecorder._bind_run_id`` — the one place
    every recorded run, WS/openai and trigger alike, learns its id).

    Token CAS (``repo.bind_probe_run``): lands only while the row is still
    PROBING under ``probe_token``, and never moves it out of PROBING. From
    then on ``_claimant_may_be_live`` asks about THIS run only, so no other
    run of the agent — an older long turn, an ungated entry's turn, a turn
    that started after the claim — can keep the claim alive (#394 review
    I-1). No-op returning False without a token. Best-effort: never raises.
    """
    if probe_token is None or not run_id:
        return False
    try:
        db = db or await get_db_client()
        return await AgentCircuitBreakerRepository(db).bind_probe_run(
            agent_id, probe_token, run_id
        )
    except Exception as e:  # noqa: BLE001 — observer never breaks the observed
        logger.warning(f"[agent-cb] bind_probe_run({agent_id}) failed: {e}")
        return False


async def release_orphaned_probe(agent_id: str, db=None) -> bool:
    """Crash-window fallback for a claim whose holder DIED and so can never
    settle it with its token (``run_recorder.sweep_stale_runs`` calls this
    after flipping a lost run). Releases the PROBING row only when its
    claimant cannot still be running (``_claimant_may_be_live``: the bound
    claimant run is no longer live, or no run was bound and the grant has
    expired). Any other run of the agent, live or not, is irrelevant.
    Best-effort: never raises."""
    try:
        db = db or await get_db_client()
        repo = AgentCircuitBreakerRepository(db)
        row = await repo.get(agent_id)
        if row is None or row.cb_status != CbStatus.PROBING.value or not row.probe_token:
            return False
        if await _claimant_may_be_live(db, row):
            return False
        return await _rearm_pause_without_verdict(
            repo, row, row.probe_token, "probe turn lost (no live claimant run)"
        )
    except Exception as e:  # noqa: BLE001 — observer never breaks the observed
        logger.warning(f"[agent-cb] release_orphaned_probe({agent_id}) failed: {e}")
        return False


async def record_success(
    agent_id: str, db=None, *, probe_token: Optional[str] = None
) -> None:
    """Record a successful turn — clears any failure streak / pause.

    Best-effort at the call site. A no-op when the agent is already clean.

    While the row is PROBING only the claim holder (``probe_token``) may
    close the breaker, by token CAS: a success from a turn that never
    claimed the probe (a run that predates the claim, an ungated entry) must
    not answer for it — that is exactly the "one probe decides" invariant.
    """
    db = db or await get_db_client()
    repo = AgentCircuitBreakerRepository(db)
    row = await repo.get(agent_id)
    if row is None:
        return
    if row.cb_status == CbStatus.PROBING.value:
        if not _holds_probe(row, probe_token):
            logger.debug(
                f"[agent-cb] agent {agent_id} success from a turn that does not "
                f"hold the half-open probe — left for the claimant to settle"
            )
            return
        if await repo.settle_probe(agent_id, probe_token, _CLEAN_STATE):
            logger.info(
                f"[agent-cb] agent {agent_id} half-open probe SUCCEEDED; "
                f"breaker closed (was paused:{row.paused_reason})"
            )
        return
    if row.cb_status == CbStatus.ACTIVE.value and row.consecutive_failure_count == 0:
        return  # already clean — skip a pointless write
    await repo.upsert_state(agent_id, _CLEAN_STATE)


async def settle_probe(
    agent_id: str,
    probe_token: Optional[str],
    *,
    succeeded: Optional[bool],
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    db=None,
) -> None:
    """Settle a half-open probe from an entry point that does NOT otherwise
    feed the breaker (the trigger paths behind ``AgentRuntimeClient`` — bus
    lane, patrol). The one settlement seam those paths share (#394 review
    C1): the SAME ``record_success`` / ``record_failure`` / ``release_probe``
    the WS/openai ``BackgroundRun`` path uses, with the same token.

    ``succeeded``: True → the probe proved the credential; False → the turn
    failed with ``error_type`` / ``error_message`` (the runtime's own error
    frame or exception type, so ``classify_agent_error`` sees the vocabulary
    it knows); None → no verdict (cancelled / interrupted).

    Only acts while this turn still holds the claim: an ordinary turn of
    these paths (no token) and a claim that was reset or re-claimed meanwhile
    both leave the breaker untouched — these entries record probe outcomes
    only, never ordinary streaks, exactly as before #117. Never raises.
    """
    if probe_token is None:
        return
    try:
        db = db or await get_db_client()
        if succeeded is None:
            await release_probe(agent_id, probe_token, db=db)
            return
        row = await AgentCircuitBreakerRepository(db).get(agent_id)
        if not _holds_probe(row, probe_token):
            return
        if succeeded:
            await record_success(agent_id, db=db, probe_token=probe_token)
        else:
            await record_failure(
                agent_id, error_type, error_message, db=db, probe_token=probe_token
            )
    except Exception as e:  # noqa: BLE001 — observer never breaks the observed
        logger.warning(f"[agent-cb] settle_probe({agent_id}) failed: {e}")


def _elapsed(value: Optional[datetime]) -> bool:
    """Whether a stored deadline has passed. A NULL deadline counts as
    elapsed — the gate fails safe toward "let the claim decide" rather than
    leaving a row nobody can ever probe or re-claim."""
    until = _as_aware_utc(value)
    return until is None or until <= utc_now()


async def should_skip(agent_id: str, db=None) -> GateVerdict:
    """Should the given agent's next real-time turn be skipped?

    PURE READ — never writes. Every trigger entry point asks this first as a
    cheap pre-filter; the scarce half-open probe is claimed separately by
    ``try_begin_probe`` at the exact point a turn is about to start (pass
    this verdict as ``prior=`` so the claim does not re-read the row). The
    two are split on purpose (GitHub #117 review): when the claim lived
    here, any caller that asked and then did NOT run a turn burned the
    single probe grant. ``message_bus_trigger._process_lane`` asks before
    several ack-and-return branches (IM-prefix skip, the @mention filter,
    rate limiting), so in an active team room the 3s bus poller reliably won
    the probe and threw it away.

    FAIL-OPEN: any read error returns ``skip=False`` with ``row_known=False``
    — a breaker glitch must never block a healthy turn.

      * PAUSED, half-open delay not elapsed → skip ``paused:<reason>``.
      * PAUSED, delay elapsed → not skipped: the window MAY be open; the
        caller proceeds and ``try_begin_probe`` decides for real.
      * PROBING, grant still live → skip ``probing`` (someone holds the
        probe).
      * PROBING, grant expired → not skipped: possibly abandoned;
        ``try_begin_probe`` re-claims it only if its claimant is not live.
      * COOLING → skip until ``cooldown_until``; lazy expiry after that (no
        claim needed — several turns passing once a cooldown elapses was the
        accepted behavior long before #117).
    """
    try:
        db = db or await get_db_client()
        row = await AgentCircuitBreakerRepository(db).get(agent_id)
    except Exception as e:  # noqa: BLE001 — fail open, never block a turn
        logger.warning(f"[agent-cb] should_skip read failed for {agent_id}: {e}")
        return GateVerdict(skip=False, reason=None)
    reason: Optional[str] = None
    if row is not None:
        if row.cb_status == CbStatus.PAUSED.value:
            if not _elapsed(row.cooldown_until):
                reason = f"paused:{row.paused_reason or 'unknown'}"
        elif row.cb_status == CbStatus.PROBING.value:
            if not _elapsed(row.cooldown_until):
                reason = "probing"
        elif row.cb_status == CbStatus.COOLING.value:
            if not _elapsed(row.cooldown_until):
                reason = "cooling"
    return GateVerdict(skip=reason is not None, reason=reason, row=row, row_known=True)


async def peek_skip(agent_id: str, *, db) -> Tuple[bool, Optional[str]]:
    """Read-only twin of ``should_skip`` for callers that will NOT run a turn.

    ``(held, reason)``: True for EVERY status other than ACTIVE — paused,
    cooling (unless its cooldown has already elapsed, which the next real turn
    would let through), and any status this function does not know (a future
    half-open ``probing``). Same fail-open contract as ``should_skip``: an
    unreadable row reads as not held.

    Exists because the turn path may CONSUME the half-open probe
    (``try_begin_probe``, GitHub #117); a send-side pre-flight such as the bus
    receipt must never consume what only a turn may consume. It is also the
    whole gate for an entry point that runs a turn but can never SETTLE a
    probe (``module_poller`` Path A reports no outcome): such an entry must
    not claim one, so it runs nothing while the agent is paused or probing. Note the one
    deliberate divergence from ``should_skip``: an expired-PAUSED row reads as
    held here ("paused:<reason>") while ``should_skip`` lets exactly one turn
    through to try the probe. Deliberately small: it reads the row and
    classifies, nothing else.

    Returns a bare ``(held, reason)`` rather than ``should_skip``'s
    ``GateVerdict`` on purpose (#394 second review M-4): a pre-flight must
    never hand its read to a claim, so there is no ``row`` to carry.
    """
    try:
        # The raw row, not the entity: the entity's enum would REJECT a status
        # this build does not know, and "unknown status" is the one case this
        # function must classify as held rather than fail open on.
        row = await db.get_one(
            AgentCircuitBreakerRepository.table_name, {"agent_id": agent_id}
        )
        status = str((row or {}).get("cb_status") or CbStatus.ACTIVE.value)
        if row is None or status == CbStatus.ACTIVE.value:
            return (False, None)
        if status == CbStatus.COOLING.value:
            until = _as_aware_utc(coerce_utc(row.get("cooldown_until")))
            if until is None or until <= utc_now():
                return (False, None)
            return (True, "cooling")
        if status == CbStatus.PAUSED.value:
            return (True, f"paused:{row.get('paused_reason') or 'unknown'}")
        return (True, status)
    except Exception as e:  # noqa: BLE001 — fail open, same as should_skip
        logger.warning(f"[agent-cb] peek_skip read failed for {agent_id}: {e}")
        return (False, None)


async def try_begin_probe(
    agent_id: str, db=None, *, prior: Optional[GateVerdict] = None
) -> TurnAdmission:
    """Claim the half-open probe for a turn that is about to START.

    The ONLY writer of PROBING. Call it after every "ack and don't run a
    turn" branch has already returned, immediately before the turn is
    created. ``allowed`` means run the turn; a refusal carries the same
    reason vocabulary as ``should_skip`` and is treated exactly like a skip
    (leave pending work un-acked). When this turn wins the probe,
    ``probe_token`` is set and the turn MUST carry it to its settlement
    (see ``TurnAdmission``).

    ``prior`` is the entry point's own ``should_skip`` verdict; when it read
    the row successfully the claim reuses that read instead of reading again
    (#394 review M1) — the CAS below makes a stale read harmless.

    FAIL-OPEN on READ errors and for rows that gate nothing (no row, ACTIVE,
    COOLING — COOLING's lazy expiry is ``should_skip``'s call, not repeated
    here). The CAS WRITE is the exception: a write error counts as "did not
    win" (fail-CLOSED, ``"probing"``), because letting a known-dead agent
    through whenever the database is unhealthy is the wrong failure
    direction — and it is logged distinctly from the read fail-open so the
    two are never confused on-call.

    Losers of the race — the common case under concurrency — get
    ``"probing"``, i.e. the "try again shortly" copy, not "go re-login":
    another turn is testing the credential right now.

    PROBING with an expired grant is re-claimable ONLY when its claimant may
    no longer be running (``_claimant_may_be_live``; binding rule #14: a
    probe turn may run for hours; the grant timer alone would double-probe
    it).
    """
    try:
        db = db or await get_db_client()
        repo = AgentCircuitBreakerRepository(db)
        if prior is not None and prior.row_known:
            row = prior.row
        else:
            row = await repo.get(agent_id)
        if row is None:
            return TurnAdmission(allowed=True, reason=None)
        if row.cb_status == CbStatus.PAUSED.value:
            if not _elapsed(row.cooldown_until):
                return TurnAdmission(
                    allowed=False,
                    reason=f"paused:{row.paused_reason or 'unknown'}",
                    window=_window_of(row),
                )
            from_status = CbStatus.PAUSED.value
        elif row.cb_status == CbStatus.PROBING.value:
            if not _elapsed(row.cooldown_until):
                return TurnAdmission(
                    allowed=False, reason="probing", window=_window_of(row)
                )
            if await _claimant_may_be_live(db, row):
                # The grant timer ran out but the probe is still running
                # (long turn) — it will settle itself; do not double-probe.
                return TurnAdmission(
                    allowed=False, reason="probing", window=_window_of(row)
                )
            from_status = CbStatus.PROBING.value
        else:
            # ACTIVE / COOLING — nothing to claim
            return TurnAdmission(allowed=True, reason=None)
    except Exception as e:  # noqa: BLE001 — fail open on READ, never block a turn
        logger.warning(f"[agent-cb] try_begin_probe read failed for {agent_id}: {e}")
        return TurnAdmission(allowed=True, reason=None)

    grant_until = utc_now() + timedelta(seconds=PROBE_GRANT_SECONDS)
    try:
        token = await repo.try_claim_probe(
            agent_id, from_status, row.probe_token, grant_until
        )
    except Exception as e:  # noqa: BLE001 — a failed WRITE is "did not win"
        logger.warning(
            f"[agent-cb] try_begin_probe CAS write failed for {agent_id}; "
            f"treating as not claimed (fail-closed): {e}"
        )
        return TurnAdmission(allowed=False, reason="probing", window=_window_of(row))
    if token is None:
        return TurnAdmission(allowed=False, reason="probing", window=_window_of(row))
    logger.info(
        f"[agent-cb] agent {agent_id} half-open probe claimed "
        f"(from={from_status}, paused:{row.paused_reason}, streak="
        f"{row.consecutive_failure_count}, grant_until={grant_until.isoformat()})"
    )
    return TurnAdmission(allowed=True, reason=None, probe_token=token)


async def admit_turn(agent_id: str, db=None) -> TurnAdmission:
    """Both gate steps at once, for an entry point that has nothing left to
    decide between "should this agent run?" and "start the turn" — the IM
    channel triggers and the A2A server call it immediately before their
    runtime call, after every branch that returns without a turn.

    ``should_skip`` (a refusal carries its reason) → ``try_begin_probe``
    reusing that read. Same contract as the two steps: a won probe comes
    back as ``probe_token``, which the caller MUST hand to its runtime call
    (``run_and_collect`` / ``run_stream`` settle it) and release on its
    exit (``release_probe`` is a no-op once settled). Fail-open on read
    errors exactly like the two steps."""
    verdict = await should_skip(agent_id, db=db)
    if verdict.skip:
        return TurnAdmission(
            allowed=False, reason=verdict.reason, window=_window_of(verdict.row)
        )
    return await try_begin_probe(agent_id, db=db, prior=verdict)


async def _claimant_may_be_live(db, row: AgentCircuitBreaker) -> bool:
    """Whether the run holding ``row``'s probe claim may still be running.

    The claimant carries its token in-process and settles with it; this is
    only asked when that cannot have happened yet — the grant expired, or a
    lost run was swept. Identity, not time (#394 review I-1): the claimant
    bound its own ``event_id`` to the claim (``bind_probe_run``), so

      * ``probe_run_id`` set → live iff THAT events row is still running
        with a fresh heartbeat (``run_is_live``). An hours-long probe
        (binding rule #14) stays claimed; no other run of the agent — older,
        newer, gated or not — counts either way.
      * ``probe_run_id`` NULL → the claimant never got as far as its run
        row. It may still be on its way there only while the grant lasts;
        after that it is gone (crashed between the claim and the row, or
        runs without a recorder, which never binds).
    """
    if row.probe_run_id:
        runs = await db.get(
            "events",
            filters={"event_id": row.probe_run_id},
            fields=["state", "last_event_at", "started_at"],
        )
        run = (runs or [None])[0]
        return bool(run) and run.get("state") == STATE_RUNNING and run_is_live(run)
    return not _elapsed(row.cooldown_until)


def describe_skip_reason(cb_reason: Optional[str]) -> str:
    """User-facing copy for a refused turn, shared by every entry point that
    tells the caller why (WS error frame, OpenAI-compatible HTTP error).
    ``cb_reason`` is ``should_skip`` / ``try_begin_probe``'s reason.

    "probing" means another turn holds — or just won — the single half-open
    probe slot, so this request lost the race: not a hard pause, so it gets
    the same "try again shortly" copy as cooling rather than "go
    re-authenticate"."""
    reason = cb_reason or ""
    if reason.startswith("paused:quota"):
        return (
            "This agent is paused after repeated quota/balance failures. "
            "Top up or reassign the Agent slot's provider, then resume the "
            "agent in Settings."
        )
    if reason.startswith("paused"):
        return (
            "This agent is paused after repeated authentication failures. "
            "Re-authenticate (codex/claude login) or assign a working API-key "
            "provider to the Agent slot, then resume the agent in Settings."
        )
    return (  # cooling / probing
        "This agent recently failed and is briefly cooling down before it "
        "will accept new messages. Please try again shortly."
    )


async def reset_agent(agent_id: str, db=None) -> None:
    """Manually clear an agent's breaker state back to ACTIVE (idempotent)."""
    db = db or await get_db_client()
    repo = AgentCircuitBreakerRepository(db)
    row = await repo.get(agent_id)
    if row is None:
        return
    await repo.upsert_state(agent_id, _CLEAN_STATE)
    logger.info(f"[agent-cb] agent {agent_id} circuit-breaker reset to active")


async def reset_for_owner(
    user_id: str, db=None, *, provider_id: Optional[str] = None
) -> int:
    """Auto-resume the owner's auth/quota-blocked agents after a key/balance
    reconfigure. Clears PAUSED and PROBING (both only ever arise from an
    auth/quota pause — PROBING is the half-open in-flight-probe state) and
    in-progress auth/quota COOLING streaks; a transient COOLING streak is
    left alone (unrelated to the key). Returns the number of agents reset.
    Best-effort.

    ``provider_id`` narrows the reset to agents whose effective ``agent``
    slot is bound to THAT provider — resolved through the providers layer's
    single overlay rule (``model_identity.resolve_agent_config_slot``), the
    same one the runtime resolver applies, never a local copy. A confirmed-working provider says
    nothing about an agent running on a different key, so a
    ``POST /{provider_id}/test`` success resumes only what it actually
    tested. The reconfigure paths (add provider, connect subscription, set a
    slot) keep the default ``None`` = every owned agent — there the user has
    just changed what the agents run on, possibly to the very provider that
    is now being unblocked.
    """
    db = db or await get_db_client()
    repo = AgentCircuitBreakerRepository(db)
    try:
        candidates = (
            await repo.find_by_status(CbStatus.PAUSED.value)
            + await repo.find_by_status(CbStatus.PROBING.value)
            + await repo.find_by_status(CbStatus.COOLING.value)
        )
        if not candidates:
            return 0
        owned = await _owner_agent_ids(db, user_id)
        reset = 0
        for cb in candidates:
            if cb.agent_id not in owned:
                continue
            # PAUSED and PROBING are always auth/quota; for COOLING only
            # clear auth/quota streaks (a transient cooldown is unrelated to
            # the key).
            if cb.cb_status == CbStatus.COOLING.value and cb.failure_category not in (
                ErrorCategory.AUTH.value,
                ErrorCategory.QUOTA.value,
            ):
                continue
            if provider_id is not None:
                slot = await resolve_agent_config_slot(
                    db, agent_id=cb.agent_id, user_id=user_id
                )
                if ((slot or {}).get("provider_id") or None) != provider_id:
                    continue
            await repo.upsert_state(cb.agent_id, _CLEAN_STATE)
            reset += 1
        if reset:
            logger.info(
                f"[agent-cb] reset {reset} agent(s) for owner {user_id} "
                + (
                    f"after provider {provider_id} tested OK"
                    if provider_id is not None
                    else "after provider reconfigure"
                )
            )
        return reset
    except Exception as e:  # noqa: BLE001 — best-effort auto-resume
        logger.warning(f"[agent-cb] reset_for_owner({user_id}) failed: {e}")
        return 0


async def _owner_agent_ids(db, user_id: str) -> set[str]:
    """agent_id set owned by a user (agents.created_by)."""
    rows = await db.get("agents", filters={"created_by": user_id})
    return {r["agent_id"] for r in rows if r and r.get("agent_id")}


# "No live probe claim" — every write that leaves PROBING (or never enters it)
# clears the claim's identity and the run bound to it together, so a later
# claim can never inherit the previous claimant's run.
_NO_CLAIM: dict = {"probe_token": None, "probe_run_id": None}

# Canonical "healthy / no streak" write, shared by success + reset paths.
_CLEAN_STATE: dict = {
    "cb_status": CbStatus.ACTIVE.value,
    "consecutive_failure_count": 0,
    "failure_category": None,
    "cooldown_until": None,
    "paused_reason": None,
    "paused_at": None,
    **_NO_CLAIM,
}
