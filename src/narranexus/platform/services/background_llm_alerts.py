"""
@file_name: background_llm_alerts.py
@author:
@date: 2026-07-07
@description: Alerting surface for LLM failures in DETACHED background tasks
(narrative updater, Step-5 entity/memory hooks).

Why this exists: before it, those paths caught every exception and continued
(``logger.exception`` then ``return None``). When the platform OpenAI key
expired (2026-07) they 401'd for ~2 weeks with no owner-facing signal and no
SQL-able trace — long memory degraded invisibly. Incident lessons #3 (don't
swallow), #4 (L2 health), #5 (audit events in the DB) all point the same way:
a background LLM failure must leave a durable record and, when it is a
credential problem the owner can fix, a de-duplicated owner-facing notice.

Two tiers, on purpose:
  - EVERY failure → a ``service_audit`` error row. Cheap, always-on, lets an
    operator ``SELECT`` "how many background LLM failures in the last N days"
    weeks later even after logs rotate.
  - OWNER-ACTIONABLE failures only → an owner inbox notice (redacted,
    cooldown-deduped): a credential error, or an out-of-credit one (empty
    balance / spent free tier, per the shared `OUT_OF_CREDIT_REASONS`).
    Transient blips (timeouts, 5xx) are not actionable by the owner, so they
    stay out of the inbox to avoid alarm fatigue.

The cooldown is persisted (``owner_notice_cooldowns``, the same row shape the
message bus's failure notices use since 2026-09-09): a restart no longer
re-notifies, and several background processes agree on one window. An
unreadable window fails OPEN — the observer must never silence the observed.
"""

from __future__ import annotations

import uuid
from typing import Optional

from loguru import logger

from narranexus.platform.agent_framework.llm.failure import (
    OUT_OF_CREDIT_REASONS,
    SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED,
    classify_self_serviceable,
    is_credential_error,
    redact_secrets,
)
from narranexus.platform.repository.inbox_repository import InboxRepository
from narranexus.platform.repository.owner_notice_cooldown_repository import (
    OwnerNoticeCooldownRepository,
)
from narranexus.platform.schema.inbox_schema import InboxMessageType, MessageSource
from narranexus.platform.services.service_audit import ServiceAuditor
from narranexus.platform.utils.db.db_factory import get_db_client

# What the owner can do about each owner-actionable category.
_CREDENTIAL_HINT = (
    "Check the agent's LLM Helper (helper_llm) provider configuration "
    "— API key and base URL — in Provider settings."
)
_BALANCE_HINT = (
    "The provider behind the LLM Helper (helper_llm) slot reports insufficient "
    "balance / quota. Top up that account or switch the slot's provider in "
    "Settings → Providers."
)
_FREE_TIER_HINT = (
    "Your free platform credit is used up. Upgrade to Nexus Pro in "
    "Settings → Account & Subscription, or add your own provider key and "
    "switch the LLM Helper slot to it in Settings → Providers."
)


def _out_of_credit_reason(error: object) -> Optional[str]:
    """The shared classifier's out-of-credit reason for ``error``, else None."""
    error_type = type(error).__name__ if isinstance(error, BaseException) else None
    reason = classify_self_serviceable(error_type, str(error) if error is not None else "")
    return reason if reason in OUT_OF_CREDIT_REASONS else None


# Name the background LLM plane records under in the service_audit table.
_AUDIT_SERVICE = "background_llm"

# One owner inbox notice per dedup key per this window. Matches the message
# bus's FAILURE_NOTIFY_COOLDOWN_SECONDS so an owner gets at most one nudge per
# key per half hour. The key depends on the category (`_dedup_key`).
ALERT_COOLDOWN_SECONDS = 1800

# Balance-class notices are keyed on the owner alone: see `_dedup_key`.
_OWNER_WIDE_TARGET = "owner"


def _dedup_key(
    category: str, *, agent_id: str, owner_user_id: str, source_id: str
) -> tuple[str, str]:
    """The (agent_id, target) half of the cooldown key for one notice.

    A credential failure is keyed per source (a narrative, a team, an entity):
    two sources may be configured differently, so two failing sources are two
    facts. An out-of-credit failure is ONE fact about the owner's account — a
    spent free tier or an empty provider balance stops every narrative, entity
    and team of every agent at once, and the remedy is the same single top-up.
    Keying it per source turned one empty balance into a notice per active
    narrative / entity candidate every half hour, the alarm fatigue this module
    exists to avoid. So balance notices collapse to one per owner per window.
    """
    if category == "provider_balance":
        return owner_user_id, _OWNER_WIDE_TARGET
    return agent_id, source_id or agent_id


async def _cooling(db, agent_id: str, target: str, category: str) -> bool:
    """Is a notice for this key still inside its window? Fails OPEN.

    The failure shape this trades for: the old process-local map deduplicated
    no matter what the DB did; this one loses dedup for as long as the table
    cannot be read. Self-limiting in practice — the inbox write that follows
    hits the same DB and fails too — and the wrong side to err on would be
    silencing a real credential failure behind an unreadable window.
    """
    try:
        return await OwnerNoticeCooldownRepository(db).is_cooling(
            agent_id, target, category, ALERT_COOLDOWN_SECONDS
        )
    except Exception as e:  # noqa: BLE001 — a notice beats a silent window
        logger.warning(f"[owner-notice] cooldown read failed for {agent_id}: {e}")
        return False


async def _arm(db, agent_id: str, target: str, category: str) -> None:
    """Open the window — called only after the inbox write succeeded."""
    try:
        await OwnerNoticeCooldownRepository(db).arm(agent_id, target, category)
    except Exception as e:  # noqa: BLE001 — best-effort; a duplicate later is the cheap failure
        logger.warning(f"[owner-notice] cooldown arm failed for {agent_id}: {e}")


async def alert_background_llm_failure(
    *,
    agent_id: str,
    source: str,
    error: object,
    owner_user_id: Optional[str] = None,
    source_id: str = "",
) -> None:
    """Record a background LLM failure and, when the owner can fix it
    (credential or out-of-credit), notify them.

    ``source`` is a short label that lands in the audit detail and the
    notice title. The list below is authoritative — an operator reading it
    is how "which sources exist?" gets answered, so **anything that starts
    emitting a new source must be added here in the same commit**:

    - ``narrative_update`` — narrative/_narrative_impl/updater.py
    - ``post_turn_hooks`` — agent_runtime/agent_runtime.py
    - ``entity_summary`` / ``entity_dedup`` / ``entity_extraction`` /
      ``description_compression`` / ``persona_inference`` — the
      social-network memory chain (plugins/builtin.social_network/.../
      social_network_module/_entity_updater.py)
    - ``team_summary`` — the team bulletin's auto-summary
      (plugins/builtin.teams/.../teams/summary_worker.py)

    (``memory_extraction`` used to be listed here and was never emitted by
    anything — a label that answers a SELECT with zero rows cannot tell an
    operator "healthy" from "renamed", which is the failure this list is
    meant to prevent.)

    NOTE the same chain also writes DB-write failures under a DIFFERENT
    audit service, ``social_network_memory`` (they are not LLM failures and
    do not page an owner). "Why did memory stop updating?" therefore spans
    two service names — see ``_entity_updater.py``'s mirror doc.
    Never raises — an observer must not break the observed path (incident
    lesson #3's corollary: the alerter is best-effort).
    """
    # Out-of-credit is asked FIRST, the same order `classify_self_serviceable`
    # uses (invalid credentials last): a 403 whose body reports an empty
    # balance, or a quota error that echoes "API key", also matches the broad
    # credential patterns, and telling that owner to re-check a working key is
    # the wrong remedy.
    credit_reason = _out_of_credit_reason(error)
    if credit_reason is not None:
        category = "provider_balance"
        hint = _FREE_TIER_HINT if credit_reason == SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED else _BALANCE_HINT
    elif is_credential_error(error):
        category, hint = "provider_credential", _CREDENTIAL_HINT
    else:
        category, hint = "generic", ""

    # Tier 1 — always leave a durable, SQL-able trace.
    try:
        await ServiceAuditor(_AUDIT_SERVICE).error(
            {
                "agent_id": agent_id,
                "source": source,
                "source_id": source_id,
                "category": category,
                "error": redact_secrets(error),
            }
        )
    except Exception as e:  # noqa: BLE001 — observer never breaks observed
        logger.warning(f"[background-llm] audit write failed: {e}")

    # Tier 2 — owner inbox notice, owner-actionable only + owner known + deduped.
    if category == "generic" or not owner_user_id:
        return

    try:
        db = await get_db_client()
        key_agent, target = _dedup_key(
            category, agent_id=agent_id, owner_user_id=owner_user_id, source_id=source_id
        )
        if await _cooling(db, key_agent, target, category):
            return
        safe_error = redact_secrets(error)
        problem = "credential" if category == "provider_credential" else "balance/quota"
        content = (
            f"A background task ({source}) for this agent is failing with what "
            f"looks like a provider {problem} error, so its background updates "
            f"are being skipped.\n\n"
            f"Error: {safe_error}\n\n"
            f"{hint} Updates resume automatically once the provider works again."
        )
        await InboxRepository(db).create_message(
            user_id=owner_user_id,
            message_id=f"bgllm_{uuid.uuid4().hex[:16]}",
            title=f"Background updates failing ({source}): {agent_id}",
            content=content,
            message_type=InboxMessageType.SYSTEM_NOTICE,
            source=MessageSource(type="background_llm_failure", id=source_id or agent_id),
        )
        # Arm cooldown only after a successful write — a transient DB blip must
        # not silently suppress the real notice for the rest of the window.
        await _arm(db, key_agent, target, category)
        logger.warning(
            f"[background-llm] notified owner {owner_user_id} of {category} "
            f"failure for agent {agent_id} (source={source})"
        )
    except Exception as e:  # noqa: BLE001 — notification is best-effort
        logger.warning(f"[background-llm] owner inbox notice failed: {e}")


# Audit plane for the real-time-layer Agent circuit-breaker.
_CB_AUDIT_SERVICE = "agent_circuit_breaker"


async def alert_agent_paused(
    *,
    agent_id: str,
    reason: str,
    error: object,
    owner_user_id: Optional[str] = None,
    source_id: str = "",
) -> None:
    """Record + notify that an Agent's real-time turns were auto-paused.

    Fired by the circuit-breaker when an agent hits the consecutive-failure
    threshold for an auth/quota cause. Both reasons are owner-actionable
    (fix a key / top up a balance), so — unlike the memory-updater alert
    above — the owner inbox notice goes out for BOTH, deduped per
    (agent_id, reason). Never raises (observer never breaks the observed).
    """
    safe_error = redact_secrets(error)

    # Tier 1 — always leave a durable, SQL-able trace.
    try:
        await ServiceAuditor(_CB_AUDIT_SERVICE).error(
            {
                "agent_id": agent_id,
                "event": "paused",
                "reason": reason,
                "source_id": source_id,
                "error": safe_error,
            }
        )
    except Exception as e:  # noqa: BLE001 — observer never breaks observed
        logger.warning(f"[agent-cb] audit write failed: {e}")

    if not owner_user_id:
        return

    if reason == "quota":
        hint = (
            "The agent's provider reports the balance/quota is exhausted. Top up "
            "or assign a provider with available quota to the Agent slot in "
            "Provider settings."
        )
    else:  # auth (and any future owner-fixable reason)
        hint = (
            "The agent's credentials look dead (expired login or invalid API "
            "key). Re-authenticate — run `codex login` / `claude login` on the "
            "host, or assign an API-key provider to the Agent slot in Provider "
            "settings."
        )
    try:
        db = await get_db_client()
        target = source_id or agent_id
        if await _cooling(db, agent_id, target, f"cb:{reason}"):
            return
        content = (
            f"Real-time replies for this agent were automatically paused after "
            f"repeated {reason} failures, to stop re-triggering a run that "
            f"cannot currently succeed.\n\n"
            f"Error: {safe_error}\n\n"
            f"{hint}\n\n"
            f"It resumes automatically once you reconfigure the provider, or "
            f"you can re-enable it manually from the agent's settings."
        )
        await InboxRepository(db).create_message(
            user_id=owner_user_id,
            message_id=f"agentcb_{uuid.uuid4().hex[:16]}",
            title=f"Agent paused ({reason}): {agent_id}",
            content=content,
            message_type=InboxMessageType.SYSTEM_NOTICE,
            source=MessageSource(type="agent_circuit_breaker", id=source_id or agent_id),
        )
        await _arm(db, agent_id, target, f"cb:{reason}")
        logger.warning(
            f"[agent-cb] notified owner {owner_user_id}: agent {agent_id} "
            f"paused ({reason})"
        )
    except Exception as e:  # noqa: BLE001 — notification is best-effort
        logger.warning(f"[agent-cb] owner inbox notice failed: {e}")


async def alert_agent_transient_streak(
    *,
    agent_id: str,
    consecutive_failures: int,
    error: object,
    owner_user_id: Optional[str] = None,
) -> None:
    """A persistently-failing PROVIDER-side (transient) agent.

    Transient failures NEVER hard-pause (binding rule #15 — the platform does
    not give up on the user's chosen model). But a model/endpoint that keeps
    failing for a long time should not degrade silently, so on a sustained
    streak we (a) leave a durable audit row and (b) send the OWNER a FACTUAL,
    NON-PRESCRIPTIVE notice: here is the error, the agent is still retrying,
    you decide. It never says "switch your model" (that would be the platform
    judging the user's choice). Deduped per (agent_id, "transient").
    Best-effort.
    """
    safe_error = redact_secrets(error)

    try:
        await ServiceAuditor(_CB_AUDIT_SERVICE).error(
            {
                "agent_id": agent_id,
                "event": "transient_streak",
                "consecutive_failures": consecutive_failures,
                "error": safe_error,
            }
        )
    except Exception as e:  # noqa: BLE001 — observer never breaks observed
        logger.warning(f"[agent-cb] transient-streak audit failed: {e}")

    if not owner_user_id:
        return

    try:
        db = await get_db_client()
        if await _cooling(db, agent_id, agent_id, "cb:transient"):
            return
        content = (
            f"This agent's real-time replies have failed {consecutive_failures} "
            f"times in a row talking to its LLM provider. It is NOT paused — it "
            f"keeps retrying automatically with backoff — but the failures look "
            f"provider/model-side, not a credential or quota problem.\n\n"
            f"Error: {safe_error}\n\n"
            f"No action is required from the platform's side; you may want to "
            f"check the provider/model configured for this agent if the failures "
            f"persist. It recovers on its own as soon as a turn succeeds."
        )
        await InboxRepository(db).create_message(
            user_id=owner_user_id,
            message_id=f"agentcb_{uuid.uuid4().hex[:16]}",
            title=f"Agent repeatedly failing: {agent_id}",
            content=content,
            message_type=InboxMessageType.SYSTEM_NOTICE,
            source=MessageSource(type="agent_circuit_breaker", id=agent_id),
        )
        await _arm(db, agent_id, agent_id, "cb:transient")
        logger.warning(
            f"[agent-cb] notified owner {owner_user_id}: agent {agent_id} "
            f"transient-failing x{consecutive_failures}"
        )
    except Exception as e:  # noqa: BLE001 — notification is best-effort
        logger.warning(f"[agent-cb] transient owner notice failed: {e}")


async def audit_agent_internal_streak(
    *,
    agent_id: str,
    consecutive_failures: int,
    error: object,
) -> None:
    """A persistently-failing agent whose failures are BUSINESS-class — our own
    pipeline bug, a permanent client error (context too long, unknown model,
    content policy), or unattributable.

    The OWNER cannot act on these, so this is PLATFORM-only: a durable audit row
    plus a loud log line tagged for us — NEVER an owner inbox notice. Best-effort.
    """
    safe_error = redact_secrets(error)
    try:
        await ServiceAuditor(_CB_AUDIT_SERVICE).error(
            {
                "agent_id": agent_id,
                "event": "internal_streak",
                "consecutive_failures": consecutive_failures,
                "error": safe_error,
            }
        )
    except Exception as e:  # noqa: BLE001 — observer never breaks observed
        logger.warning(f"[agent-cb] internal-streak audit failed: {e}")
    # Loud, greppable, platform-facing — this likely needs OUR attention.
    logger.error(
        f"[agent-cb][PLATFORM] agent {agent_id} failing x{consecutive_failures} "
        f"with a non-provider (business/internal) error — likely our bug or a "
        f"permanent client error, NOT owner-actionable: {safe_error}"
    )
