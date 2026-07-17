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
  - CREDENTIAL-class failures only → an owner inbox notice (redacted,
    cooldown-deduped). Transient blips (timeouts, 5xx) are not actionable by
    the owner, so they stay out of the inbox to avoid alarm fatigue.

The cooldown map is in-process (a restart resets it) — the same accepted
tradeoff the message bus already makes for its failure notices.
"""

from __future__ import annotations

import time
import uuid
from typing import Dict, Optional

from loguru import logger

from xyz_agent_context.agent_framework.llm_failure import (
    is_credential_error,
    redact_secrets,
)
from xyz_agent_context.repository.inbox_repository import InboxRepository
from xyz_agent_context.schema.inbox_schema import InboxMessageType, MessageSource
from xyz_agent_context.services.service_audit import ServiceAuditor
from xyz_agent_context.utils.db_factory import get_db_client

# Name the background LLM plane records under in the service_audit table.
_AUDIT_SERVICE = "background_llm"

# One owner inbox notice per (agent_id, category) per this window. Matches the
# message bus's FAILURE_NOTIFY_COOLDOWN_SECONDS so an owner running many
# background paths for one broken key gets at most one nudge per surface per
# half hour.
ALERT_COOLDOWN_SECONDS = 1800

# (agent_id:category) -> last owner-notice monotonic timestamp.
_notify_cooldown: Dict[str, float] = {}


def reset_alert_state() -> None:
    """Clear the in-process cooldown map. For tests / explicit resets."""
    _notify_cooldown.clear()


async def alert_background_llm_failure(
    *,
    agent_id: str,
    source: str,
    error: object,
    owner_user_id: Optional[str] = None,
    source_id: str = "",
) -> None:
    """Record a background LLM failure and, if credential-class, notify the owner.

    ``source`` is a short label ("narrative_update", "entity_summary",
    "memory_extraction") that lands in the audit detail and the notice title.
    Never raises — an observer must not break the observed path (incident
    lesson #3's corollary: the alerter is best-effort).
    """
    is_credential = is_credential_error(error)
    category = "provider_credential" if is_credential else "generic"

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

    # Tier 2 — owner inbox notice, credential-class only + owner known + deduped.
    if not is_credential or not owner_user_id:
        return

    cooldown_key = f"{agent_id}:{category}"
    now = time.monotonic()
    last = _notify_cooldown.get(cooldown_key)
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
        return

    try:
        db = await get_db_client()
        safe_error = redact_secrets(error)
        content = (
            f"A background task ({source}) for this agent is failing with what "
            f"looks like a provider/credential error, so its long-memory "
            f"updates are being skipped.\n\n"
            f"Error: {safe_error}\n\n"
            f"Check the agent's LLM Helper (helper_llm) provider configuration "
            f"— API key and base URL — in Provider settings. Updates resume "
            f"automatically once the credentials work again."
        )
        await InboxRepository(db).create_message(
            user_id=owner_user_id,
            message_id=f"bgllm_{uuid.uuid4().hex[:16]}",
            title=f"Background memory updates failing: {agent_id}",
            content=content,
            message_type=InboxMessageType.SYSTEM_NOTICE,
            source=MessageSource(type="background_llm_failure", id=source_id or agent_id),
        )
        # Arm cooldown only after a successful write — a transient DB blip must
        # not silently suppress the real notice for the rest of the window.
        _notify_cooldown[cooldown_key] = now
        logger.warning(
            f"[background-llm] notified owner {owner_user_id} of credential "
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

    cooldown_key = f"cb:{agent_id}:{reason}"
    now = time.monotonic()
    last = _notify_cooldown.get(cooldown_key)
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
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
        _notify_cooldown[cooldown_key] = now
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

    cooldown_key = f"cb:{agent_id}:transient"
    now = time.monotonic()
    last = _notify_cooldown.get(cooldown_key)
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
        return

    try:
        db = await get_db_client()
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
        _notify_cooldown[cooldown_key] = now
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
