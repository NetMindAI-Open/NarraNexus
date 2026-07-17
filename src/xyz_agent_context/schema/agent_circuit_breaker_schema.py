"""
@file_name: agent_circuit_breaker_schema.py
@author:
@date: 2026-07-13
@description: Data model for the real-time-layer Agent circuit-breaker.

The circuit-breaker stops re-triggering an Agent whose real-time turns keep
failing (dead OAuth/API key, exhausted balance, model unavailable). State
lives in the independent ``instance_agent_circuit_breaker`` table, keyed by
``agent_id`` (NOT columns on ``agents``).

Escalation splits by classified cause (see ``ErrorCategory``):
- auth / quota  → won't self-heal; PAUSE after a few consecutive failures so
  the owner is told to fix a key/balance, then auto-resume on reconfigure.
- transient / business → self-healing (or the user's chosen flaky model,
  which binding rule #15 forbids the platform from giving up on); these NEVER
  hard-pause — they cool with exponential backoff and retry forever.

Binding rules #14/#15: the breaker only reacts to turns that already FAILED,
pauses the SCHEDULING of new turns, and never cancels an in-flight loop.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class CbStatus(str, Enum):
    """Circuit-breaker state for one Agent's real-time turns."""
    ACTIVE = "active"      # normal — new turns are allowed
    COOLING = "cooling"    # recent failure(s); skip until cooldown_until elapses
    PAUSED = "paused"      # hard stop; only auth/quota reach this. Needs reset.


class PausedReason(str, Enum):
    """Why an Agent is PAUSED. Only auth/quota ever hard-pause, so there is no
    ``repeated_failure`` reason — a merely-flaky (transient) agent is never
    hard-paused (binding rule #15)."""
    AUTH = "auth"      # credentials dead (401/403/expired login) — needs a key
    QUOTA = "quota"    # balance/free-tier exhausted, no provider — needs top-up


class ErrorCategory(str, Enum):
    """Classification of a failed turn. Drives the escalation split.

    QUOTA is the extension point for the future "Executor batch balance
    insufficient" case — a balance error just needs to map into it. BUSINESS
    is reserved (our-own-bug / permanent business error); it is currently
    treated like TRANSIENT (cools but never hard-pauses).
    """
    AUTH = "auth"
    QUOTA = "quota"
    TRANSIENT = "transient"
    BUSINESS = "business"


# Categories that objectively need the user to change something (key/balance)
# and therefore escalate to a hard PAUSE. The rest cool + retry forever.
PAUSING_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {ErrorCategory.AUTH, ErrorCategory.QUOTA}
)


# =============================================================================
# Model
# =============================================================================

class AgentCircuitBreaker(BaseModel):
    """One row of ``instance_agent_circuit_breaker`` — the breaker state for a
    single Agent's real-time dialogue turns."""

    agent_id: str
    cb_status: CbStatus = CbStatus.ACTIVE
    consecutive_failure_count: int = 0
    # The category the current failure streak belongs to. Lets us count
    # *consecutive same-category* failures and reset the streak when the
    # category changes — so "3 consecutive auth failures" can never be
    # contaminated by an unrelated transient blip in between.
    failure_category: Optional[ErrorCategory] = None
    cooldown_until: Optional[datetime] = None
    paused_reason: Optional[PausedReason] = None
    paused_at: Optional[datetime] = None
    last_error: Optional[str] = None  # already redacted before it lands here
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"use_enum_values": True}
