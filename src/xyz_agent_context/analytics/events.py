"""
@file_name: events.py
@date: 2026-06-08
@description: Funnel event names + shared property keys (single source of
truth, avoids string drift across capture sites).
"""
from __future__ import annotations

# Funnel events (local/desktop this phase; cloud deferred). The lean funnel:
# signed_up -> setup_entered -> (setup_skipped | setup_completed)
#           -> message_round_trip_succeeded.
# The setup_* events are pure UI actions reported by the frontend via the
# backend POST /api/auth/funnel endpoint (so they inherit opt-out, distinct_id
# hashing, and the surface label). The two backend-native events (signed_up,
# message_round_trip_succeeded) are emitted directly at their source.
EVENT_SIGNED_UP = "signed_up"
EVENT_SETUP_ENTERED = "setup_entered"
EVENT_SETUP_SKIPPED = "setup_skipped"
EVENT_SETUP_COMPLETED = "setup_completed"
EVENT_MESSAGE_ROUND_TRIP_SUCCEEDED = "message_round_trip_succeeded"

# Standard property keys.
PROP_SURFACE = "surface"
PROP_METHOD = "method"       # signed_up: "create_user"
PROP_AGENT_ID = "agent_id"   # message_round_trip_succeeded
PROP_RUN_ID = "run_id"       # message_round_trip_succeeded
