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
EVENT_DEDUP_FAIL_OPEN = "dedup_fail_open"
EVENT_DEBOUNCE_MERGED = "debounce_merged"

# ─── Subscriber lifecycle ─────────────────────────────────────────────────
EVENT_SUBSCRIBER_STARTED = "subscriber_started"
EVENT_SUBSCRIBER_STOPPED = "subscriber_stopped"

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
