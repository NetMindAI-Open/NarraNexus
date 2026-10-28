"""
@file_name: channel_audit_events.py
@date: 2026-05-08
@description: Generic IM channel trigger audit event-type constants.

Module-level string constants (NOT an enum) so:
1. Callers can grep for them as plain strings.
2. The DB column stays a simple VARCHAR — adding a new event type does
   not require a schema migration.

Naming versus Lark's pre-existing `lark_trigger_audit_repository.py`:
- ``transport_*`` replaces ``ws_*`` because Telegram uses long polling and
  Slack offers Socket Mode + Event API — none of those are exclusively
  WebSocket. Lark's existing audit table keeps the ``ws_*`` strings until
  Phase 2 migrates it to write into ``channel_trigger_audit``.
- ``debounce_merged`` is new in Phase 1. There is no Lark equivalent
  because the existing trigger does not implement debounce.
"""
from __future__ import annotations

# ─── Managed ingress (Manyfold-hosted turns) ─────────────────────────────
# The managed surface's lifecycle in the same table as native ingress:
# a managed message that produced nothing must be as answerable as a
# native one (lesson #5 — "the N expected events are all missing" is
# itself evidence). `managed_ingress_processed` (the run-completed row,
# written by ChannelTriggerBase.managed_after_run) predates the rest.
EVENT_MANAGED_INGRESS_PROCESSED = "managed_ingress_processed"
EVENT_MANAGED_INGRESS_DENIED = "managed_ingress_denied"
EVENT_MANAGED_INGRESS_SILENT = "managed_ingress_silent"
EVENT_MANAGED_ATTACHMENTS = "managed_attachments_converted"
# Manyfold's files/write ingest leg (backend/routes/manyfold/files.py):
# channel column carries "manyfold". Every write attempt gets a row —
# the 2026-08-05 staging diagnosis had to infer write outcomes from the
# PLATFORM's side because our own gateway kept no account.
EVENT_MANYFOLD_FILES_WRITE = "manyfold_files_write"

# ─── Ingress ─────────────────────────────────────────────────────────────
EVENT_INGRESS_PROCESSED = "ingress_processed"
EVENT_INGRESS_DROPPED_DEDUP = "ingress_dropped_dedup"
EVENT_INGRESS_DROPPED_HISTORIC = "ingress_dropped_historic"
EVENT_INGRESS_DROPPED_ECHO = "ingress_dropped_echo"
EVENT_INGRESS_DROPPED_UNBOUND = "ingress_dropped_unbound"
# parse_event returned None (unsupported message type: sticker / image /
# voice on a text-only channel). Was a bare `continue` with zero trace —
# unanswerable "why didn't the bot reply?" tickets (lessons #3/#5).
EVENT_INGRESS_DROPPED_UNPARSED = "ingress_dropped_unparsed"
# parse_event succeeded but yielded NEITHER text NOR attachment refs, so
# the pipeline has nothing to run the agent on. Same audit-blind-spot
# class as `unparsed` above: the guard was a bare `return`, and a payload
# shape the extractor didn't recognise (live incident 2026-08-06: a post
# body without the language wrapper) vanished without a trace.
EVENT_INGRESS_DROPPED_EMPTY = "ingress_dropped_empty"
# A group-room message that did not @-mention this bot. Once a bot holds a
# read-all-group-messages scope EVERY group message reaches the subscriber,
# and replying to all of them is the single most visible misbehaviour a
# channel bot can have. Dropped at ingress rather than left to the model to
# judge — but audited, because "the bot ignored me in the group" must stay
# answerable from the trace (lessons #3/#5).
EVENT_INGRESS_DROPPED_NOT_MENTIONED = "ingress_dropped_not_mentioned"
EVENT_DEDUP_FAIL_OPEN = "dedup_fail_open"
EVENT_DEBOUNCE_MERGED = "debounce_merged"

# ─── Ingress circuit breaker (2026-08-24) ────────────────────────────────
# The 8/14 DM ping-pong loop ran 70+ hours behind an all-green dashboard
# because nothing on the inbound path asked "is this message worth
# processing?". These three make the breaker's whole life legible from the
# DB alone: the moment it closed the door, every message that hit the
# closed door, and the moment it opened again.
#
# ``tripped`` covers both the first trip and each escalation — the row's
# ``details.transition`` tells them apart, matching how the subscriber
# breaker keeps one event type and varies ``details``.
EVENT_INGRESS_BREAKER_TRIPPED = "ingress_breaker_tripped"
# Cooldown elapsed and one half-open probe was admitted, OR the session
# behaved long enough to walk a tier back down. ``details.reason``
# distinguishes ``cooldown_expired`` from ``recovered``.
EVENT_INGRESS_BREAKER_CLEARED = "ingress_breaker_cleared"
# One message dropped because its conversation is cooling. Written per
# message ON PURPOSE: "the bot went quiet for six hours" has to be
# answerable, and a silent return is exactly the blind spot that let the
# original incident run unnoticed (lessons #3/#5).
EVENT_INGRESS_DROPPED_BREAKER = "ingress_dropped_breaker"

# ─── Subscriber lifecycle ─────────────────────────────────────────────────
EVENT_SUBSCRIBER_STARTED = "subscriber_started"
EVENT_SUBSCRIBER_STOPPED = "subscriber_stopped"
# Fast-death circuit breaker (2026-08-04): a subscriber that keeps dying
# within seconds of starting (cleared secret → silent return, no exception
# for is_permanent_auth_failure to see) is isolated instead of being
# restarted every poll forever. One row per trip / clear, replacing the
# unbounded death/rebirth WARNING spam this shipped to fix.
EVENT_SUBSCRIBER_BREAKER_TRIPPED = "subscriber_breaker_tripped"
EVENT_SUBSCRIBER_BREAKER_CLEARED = "subscriber_breaker_cleared"
# Pre-flight rejection: the credential is bound but provably cannot connect
# (Lark: App Secret cleared), so no subscriber is started at all. Written
# once per credential state — the breaker never has to absorb a restart
# storm for a condition knowable before the first start.
EVENT_SUBSCRIBER_UNSTARTABLE = "subscriber_unstartable"

# ─── Transport-layer events (renamed from Lark's ws_*) ────────────────────
EVENT_TRANSPORT_CONNECTED = "transport_connected"
EVENT_TRANSPORT_DISCONNECTED = "transport_disconnected"
EVENT_TRANSPORT_BACKOFF = "transport_backoff"
# Reply-side transport failure (added 2026-07-02 for MatrixTrigger, but
# generic — any channel whose reply path can fail out-of-band after the
# agent finished should emit this). Distinct from ``inbox_write_failed``:
# that one is about our own DB row; this one is about the platform
# refusing / dropping our outbound message. Details typically carry
# ``error_code`` (M_LIMIT_EXCEEDED / M_UNKNOWN_TOKEN / network / …),
# ``attempts``, and the truncated reply body for post-mortem.
EVENT_TRANSPORT_SEND_FAILED = "transport_send_failed"

# ─── Worker pool ──────────────────────────────────────────────────────────
EVENT_WORKER_ERROR = "worker_error"
EVENT_WORKER_TIMEOUT = "worker_timeout"

# ─── Inbox / observability ────────────────────────────────────────────────
EVENT_INBOX_WRITE_FAILED = "inbox_write_failed"
EVENT_HEARTBEAT = "heartbeat"

# ─── Attachment ingestion (Phase 1a) ─────────────────────────────────────
# Emitted by ChannelTriggerBase.fetch_attachments and _persist_attachment
# when handling inbound media (PDFs, images, voice memos, etc.). See
# .claude/PRPs/plans/im-multimodal-ingest.plan.md for the full design.
EVENT_INGRESS_DROPPED_OVERSIZED = "ingress_dropped_oversized"
EVENT_ATTACHMENT_FETCH_FAILED = "attachment_fetch_failed"
EVENT_ATTACHMENT_PERSISTED = "attachment_persisted"
