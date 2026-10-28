"""
@file_name: events.py
@date: 2026-06-08
@description: Funnel event names + shared property keys (single source of
truth, avoids string drift across capture sites).
"""
from __future__ import annotations

# Product events are persisted only in the first-party database for the
# current surface. No vendor telemetry sink receives them.
EVENT_SIGNED_UP = "signed_up"
EVENT_SETUP_ENTERED = "setup_entered"
EVENT_SETUP_SKIPPED = "setup_skipped"
EVENT_SETUP_COMPLETED = "setup_completed"
EVENT_WORKSPACE_READY = "workspace_ready"
EVENT_MESSAGE_SUBMITTED = "message_submitted"
EVENT_MESSAGE_ACCEPTED = "message_accepted"
EVENT_RUN_STARTED = "run_started"
EVENT_MESSAGE_ROUND_TRIP_SUCCEEDED = "message_round_trip_succeeded"
EVENT_MESSAGE_ROUND_TRIP_FAILED = "message_round_trip_failed"
EVENT_REPLY_RENDERED = "reply_rendered"
EVENT_SUBSCRIBE_CLICKED = "subscribe_clicked"
EVENT_CHECKOUT_CREATED = "checkout_created"
EVENT_CHECKOUT_OPENED = "checkout_opened"
EVENT_SUBSCRIPTION_ACTIVATED = "subscription_activated"

FRONTEND_EVENTS = frozenset({
    EVENT_SETUP_ENTERED,
    EVENT_SETUP_SKIPPED,
    EVENT_SETUP_COMPLETED,
    EVENT_WORKSPACE_READY,
    EVENT_MESSAGE_SUBMITTED,
    EVENT_REPLY_RENDERED,
    EVENT_SUBSCRIBE_CLICKED,
    EVENT_CHECKOUT_OPENED,
})

# Standard property keys.
PROP_SURFACE = "surface"
PROP_METHOD = "method"       # signed_up: "create_user"
PROP_AGENT_ID = "agent_id"   # message_round_trip_succeeded
PROP_RUN_ID = "run_id"       # message_round_trip_succeeded
PROP_SOURCE = "source"
PROP_SESSION_ID = "session_id"
# checkout_created. Without these, a $19 card subscription and a $228 twelve-
# month one-time purchase are indistinguishable in the funnel, so "did adding
# Alipay and WeChat sell anything" can only be answered by hand in Stripe.
# NOT part of the idempotency key — the same session must stay one event.
PROP_PAYMENT_METHOD = "payment_method"
PROP_MONTHS = "months"
PROP_TRIGGER_SOURCE = "trigger_source"
PROP_PROVIDER_CARD_SOURCE = "provider_card_source"
PROP_FAILURE_CATEGORY = "failure_category"
PROP_FAILURE_REASON = "failure_reason"
PROP_LATENCY_MS = "latency_ms"
