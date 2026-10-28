"""
@file_name: test_official_sdk_turn_delivery_tracking.py
@date: 2026-09-10
@description: official_sdk.py's ``_translate_and_track_delivery`` must
override the codex_official translator's conservative ``fatal: True``
default on a failed turn to ``False`` when the turn already streamed an
assistant message before the failure landed.

This drives real ``item/agentMessage/delta`` -> ``turn/completed
(status="failed")`` notification sequences through the ACTUAL
translation function (``output_transfer`` is not mocked) — the same
function ``CodexSDKv2.agent_loop`` calls per-notification while
streaming. Without this override, response_processor.py's ``fatal``
contract ("this turn is terminal AND nothing was delivered this turn")
would be violated for codex: a 5xx/retry-exhaustion failure landing
AFTER the agent already replied would still report ``fatal: True`` and
erase that already-delivered reply's non-fatal classification
(response_processor.py's "recovered_after_reply" severity, same-day
mirror entry).
"""
from __future__ import annotations

from narranexus.contracts.agent_events import DATA_TYPE_ERROR, DATA_TYPE_TEXT_DELTA
from narranexus_plugins.frameworks_codex_cli.official_sdk import (
    _translate_and_track_delivery,
)


def _drive(notifications: list[dict]) -> list[dict]:
    """Feed a scripted notification sequence through the real
    per-notification tracking function, threading ``turn_had_message``
    exactly like ``agent_loop``'s streaming loop does."""
    turn_had_message = False
    events: list[dict] = []
    for dump in notifications:
        translated_batch, turn_had_message = _translate_and_track_delivery(
            dump,
            transfer_type="codex_official",
            streaming=True,
            turn_had_message=turn_had_message,
        )
        events.extend(translated_batch)
    return events


def _only_error_event(events: list[dict]) -> dict:
    error_events = [e for e in events if (e.get("data") or {}).get("type") == DATA_TYPE_ERROR]
    assert len(error_events) == 1
    return error_events[0]


def test_fatal_is_false_when_a_message_preceded_the_failure():
    notifications = [
        {"method": "item/agentMessage/delta", "payload": {"delta": "hello there"}},
        {
            "method": "turn/completed",
            "payload": {
                "turn": {
                    "status": "failed",
                    "error": {"message": "boom", "type": "server_error"},
                }
            },
        },
    ]
    events = _drive(notifications)
    # Sanity: the delta really did translate to a text-delta event, proving
    # this is a real translation run, not a stub.
    assert any((e.get("data") or {}).get("type") == DATA_TYPE_TEXT_DELTA for e in events)
    assert _only_error_event(events)["data"]["fatal"] is False


def test_fatal_stays_true_when_no_message_preceded_the_failure():
    notifications = [
        {
            "method": "turn/completed",
            "payload": {
                "turn": {
                    "status": "failed",
                    "error": {"message": "boom", "type": "server_error"},
                }
            },
        },
    ]
    events = _drive(notifications)
    assert _only_error_event(events)["data"]["fatal"] is True


def test_an_empty_delta_does_not_count_as_a_delivered_message():
    """The translator itself drops empty deltas (``if not delta: return
    []``) — this locks in that the tracking function inherits that
    filtering rather than flipping ``turn_had_message`` on an empty
    string that never produced a visible event."""
    notifications = [
        {"method": "item/agentMessage/delta", "payload": {"delta": ""}},
        {
            "method": "turn/completed",
            "payload": {
                "turn": {
                    "status": "failed",
                    "error": {"message": "boom", "type": "server_error"},
                }
            },
        },
    ]
    events = _drive(notifications)
    assert _only_error_event(events)["data"]["fatal"] is True
