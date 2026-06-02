"""
@file_name: test_audit_events_attachment.py
@date: 2026-05-20
@description: Stability-pin the new Phase 1a attachment-ingestion
audit event constants. The strings live in DB rows
(``channel_trigger_audit.event_type``) and in ops dashboards — renaming
them silently would break downstream queries.
"""
from __future__ import annotations

from xyz_agent_context.channel import channel_audit_events as ev


def test_attachment_audit_event_strings_are_stable() -> None:
    """Pin the wire-format strings. If you change these, you also have
    to update every dashboard / saved query that grouped on them."""
    assert ev.EVENT_INGRESS_DROPPED_OVERSIZED == "ingress_dropped_oversized"
    assert ev.EVENT_ATTACHMENT_FETCH_FAILED == "attachment_fetch_failed"
    assert ev.EVENT_ATTACHMENT_PERSISTED == "attachment_persisted"


def test_attachment_audit_event_names_are_under_32_chars() -> None:
    """``channel_trigger_audit.event_type`` is a VARCHAR with an index;
    keeping names short keeps the index lean. 32 chars is the soft cap
    documented in channel_audit_events.py."""
    for name in (
        ev.EVENT_INGRESS_DROPPED_OVERSIZED,
        ev.EVENT_ATTACHMENT_FETCH_FAILED,
        ev.EVENT_ATTACHMENT_PERSISTED,
    ):
        assert len(name) <= 32, f"{name!r} exceeds 32-char soft cap"


def test_attachment_audit_events_are_distinct_from_existing() -> None:
    """No accidental collision with existing constants."""
    existing = {
        ev.EVENT_INGRESS_PROCESSED,
        ev.EVENT_INGRESS_DROPPED_DEDUP,
        ev.EVENT_INGRESS_DROPPED_HISTORIC,
        ev.EVENT_INGRESS_DROPPED_ECHO,
        ev.EVENT_INGRESS_DROPPED_UNBOUND,
        ev.EVENT_DEDUP_FAIL_OPEN,
        ev.EVENT_DEBOUNCE_MERGED,
        ev.EVENT_SUBSCRIBER_STARTED,
        ev.EVENT_SUBSCRIBER_STOPPED,
        ev.EVENT_TRANSPORT_CONNECTED,
        ev.EVENT_TRANSPORT_DISCONNECTED,
        ev.EVENT_TRANSPORT_BACKOFF,
        ev.EVENT_WORKER_ERROR,
        ev.EVENT_WORKER_TIMEOUT,
        ev.EVENT_INBOX_WRITE_FAILED,
        ev.EVENT_HEARTBEAT,
    }
    new = {
        ev.EVENT_INGRESS_DROPPED_OVERSIZED,
        ev.EVENT_ATTACHMENT_FETCH_FAILED,
        ev.EVENT_ATTACHMENT_PERSISTED,
    }
    assert not (existing & new), f"name collision: {existing & new}"
