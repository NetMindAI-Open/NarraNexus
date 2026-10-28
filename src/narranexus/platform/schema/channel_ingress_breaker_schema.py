"""
@file_name: channel_ingress_breaker_schema.py
@author:
@date: 2026-08-24
@description: Durable state model for the message-ingress circuit breaker.

The ingress breaker answers a question nothing else on the inbound path
asks: **is this message worth processing at all?** It is the peer of the
real-time-layer Agent breaker (``agent_circuit_breaker_schema``) and of the
subscriber fast-death breaker in ``channel/channel_trigger_base.py``:

- Agent breaker      → "my own turns keep failing, stop scheduling them"
- Fast-death breaker → "my own credential is broken, stop restarting it"
- **Ingress breaker** → "the messages arriving are not worth processing"

One row per session key ``agent_id|channel|chat_id|sender_id``. Only TIER TRANSITIONS
are written here; the sliding-window counters and content fingerprints that
drive those transitions stay in process memory (a row per inbound message
would be pure write amplification). What must survive a restart is the
cooldown — a 24h isolation that resets on every redeploy is not an
isolation at all.

Binding rules #14/#15: this gates INGRESS of new messages. It never caps,
cancels, or time-limits a running ``agent_loop``, and it never judges the
agent's own model or output — only the inbound traffic shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# Per-component clamps, mirroring each column's own width in
# ``schema_registry``. ``chat_id`` / ``sender_id`` come from the platform
# with no length contract of their own, so the key's width otherwise rests
# on an assumption: on a MySQL deployment with strict mode disabled an
# over-long key is silently truncated, letting two different conversations
# collide on the unique index and overwrite each other's tier and cooldown.
#
# These must be PER-COLUMN, not one number for all four. Clamping every
# part to 128 (the first attempt) allows 4*128 + 3 = 515 — over the 448
# the column was sized for, because ``channel`` is only VARCHAR(32). The
# comment claiming the arithmetic was "true by construction" was written
# without running the multiplication; ``test_session_key_cannot_overflow``
# now derives the bound from the registry instead of restating it here.
_MAX_AGENT_ID = 128
_MAX_CHANNEL = 32
_MAX_CHAT_ID = 128
_MAX_SENDER_ID = 128


def session_key(
    agent_id: str, channel: str, chat_id: str, sender_id: str
) -> str:
    """The breaker's identity: "who is speaking to WHOM, in which room, on
    which channel".

    Single definition so the in-memory cache, the DB row, and the audit
    trail can never key the same conversation three different ways.

    ``agent_id`` is part of the key, matching how ``ChannelDedupStore``
    partitions its own layers — and for the same reason. One trigger
    instance serves EVERY bound credential, and a single room event fans
    out to every member agent's client, so ``_process_message`` runs once
    per agent for what a human sees as one message.

    Leaving ``agent_id`` out (the first version of this file) collapsed
    those N calls onto one session: the window counted N x the real
    traffic, and since the fingerprint is also agent-independent, all N
    copies read as verbatim repeats. The duplicate ratio became
    ``1 - 1/N`` — a function of how many of OUR agents sit in the room,
    not of what the sender actually said. Five agents in a room tripped
    the breaker on a human's fourth DISTINCT message, and every agent in
    the room went deaf to them for up to 24h.
    """
    return "|".join(
        (
            agent_id[:_MAX_AGENT_ID],
            channel[:_MAX_CHANNEL],
            chat_id[:_MAX_CHAT_ID],
            sender_id[:_MAX_SENDER_ID],
        )
    )


class ChannelIngressBreaker(BaseModel):
    """One row of ``channel_ingress_breaker`` — the durable half of one
    session key's breaker state.

    ``tier`` is the escalation memory, deliberately kept across a release
    (see ``_breaker_release`` in ``channel_trigger_base.py`` for the same
    idea on the credential breaker): a session that clears its cooldown and
    immediately misbehaves again must land on the NEXT step of the schedule,
    not restart at the shortest one.

    ``tier`` semantics:
      0 — closed. Traffic flows; nothing is suppressed.
      1+ — has tripped ``tier`` times. ``cooldown_until`` is when the next
           half-open probe is allowed through.
    """

    session_key: str
    channel: str
    agent_id: Optional[str] = None
    chat_id: Optional[str] = None
    sender_id: Optional[str] = None
    tier: int = 0
    cooldown_until: Optional[datetime] = None
    # Messages dropped during the CURRENT cooldown. Reset on each trip so
    # the number answers "how much did this isolation actually absorb?" —
    # the headline figure in the owner alert and the audit row.
    suppressed_count: int = 0
    last_reason: Optional[str] = None
    last_tripped_at: Optional[datetime] = None
    # When ``tier`` last moved, in the guard's clock. The anchor for
    # ageing a silent session's tier down — see
    # ``IngressGuard._decay_for_silence``.
    tier_changed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
