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
EVENT_DEDUP_FAIL_OPEN = "dedup_fail_open"
EVENT_DEBOUNCE_MERGED = "debounce_merged"

# ─── Subscriber lifecycle ─────────────────────────────────────────────────
EVENT_SUBSCRIBER_STARTED = "subscriber_started"
EVENT_SUBSCRIBER_STOPPED = "subscriber_stopped"

# ─── Transport-layer events (renamed from Lark's ws_*) ────────────────────
EVENT_TRANSPORT_CONNECTED = "transport_connected"
EVENT_TRANSPORT_DISCONNECTED = "transport_disconnected"
EVENT_TRANSPORT_BACKOFF = "transport_backoff"

# ─── Worker pool ──────────────────────────────────────────────────────────
EVENT_WORKER_ERROR = "worker_error"
EVENT_WORKER_TIMEOUT = "worker_timeout"

# ─── Inbox / observability ────────────────────────────────────────────────
EVENT_INBOX_WRITE_FAILED = "inbox_write_failed"
EVENT_HEARTBEAT = "heartbeat"
