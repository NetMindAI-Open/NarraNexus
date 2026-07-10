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
