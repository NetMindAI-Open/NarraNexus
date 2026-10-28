"""
@file_name: test_rtc_voice_input.py
@date: 2026-08-06
@description: F28 RTC voice-input metadata — v1 parser + body envelope splitter.

Contract source: Hybrid "Direct Matrix RTC fast reply" handoff, sections 3.1/3.2/4.2.

Locks:
- A valid v1 payload parses into RtcVoiceInputV1 with all four binding IDs.
- Every §3.2 validation rule is strict: version === 1, transport === "matrix",
  seq === 1, transcript_final === true, four IDs are non-blank strings.
  Any violation -> None (event is treated as a normal text message upstream).
- Booleans are not accepted where ints are required (True == 1 in Python).
- voice_instructions is optional: missing / blank / wrong type degrades to
  None WITHOUT rejecting the voice turn itself.
- An audio message carrying only "ai.netmind.voice" never enters RTC mode.
- split_narra_system_prompt() separates the <narra-system-prompt> envelope
  from the transcript; malformed / absent envelope leaves the body untouched
  (raw transcript must never be promoted to system content).
- Common trigger carve-out: transcript_final IS False (identity) means an
  interim STT fragment — never degraded voice; 0 / "false" / missing still
  fall through. Returned instructions are capped at 2000 chars.
"""
from __future__ import annotations

import copy

from narranexus_plugins.narramessenger_module._rtc_voice import (
    RTC_VOICE_INPUT_KEY,
    RtcVoiceInputV1,
    extract_common_voice_instructions,
    parse_rtc_voice_input,
    split_narra_system_prompt,
)

# --- fixtures -------------------------------------------------------------

ENVELOPE_BODY = (
    '<narra-system-prompt version="1" mode="voice">\n'
    "Reply for a real-time voice call.\n"
    "</narra-system-prompt>\n"
    "\n"
    "What is the weather today?"
)


def make_valid_event(**meta_overrides) -> dict:
    """Verbatim shape of handoff §3.1, deep-copied per test."""
    event = {
        "type": "m.room.message",
        "room_id": "!room:matrix.example",
        "event_id": "$voice-input-event",
        "sender": "@human:matrix.example",
        "content": {
            "msgtype": "m.text",
            "body": ENVELOPE_BODY,
            RTC_VOICE_INPUT_KEY: {
                "version": 1,
                "rtc_session_id": "rtc-session-id",
                "turn_id": "turn-id",
                "invocation_id": "invocation-id",
                "agent_profile_id": "agent-profile-id",
                "seq": 1,
                "transcript_final": True,
                "transport": "matrix",
                "voice_instructions": "Reply for a real-time voice call.",
            },
        },
    }
    event = copy.deepcopy(event)
    event["content"][RTC_VOICE_INPUT_KEY].update(meta_overrides)
    return event


# --- valid payloads -------------------------------------------------------


def test_valid_payload_parses_with_all_binding_ids():
    parsed = parse_rtc_voice_input(make_valid_event())
    assert isinstance(parsed, RtcVoiceInputV1)
    assert parsed.rtc_session_id == "rtc-session-id"
    assert parsed.turn_id == "turn-id"
    assert parsed.invocation_id == "invocation-id"
    assert parsed.agent_profile_id == "agent-profile-id"
    assert parsed.voice_instructions == "Reply for a real-time voice call."


def test_missing_voice_instructions_still_enters_voice_mode():
    event = make_valid_event()
    del event["content"][RTC_VOICE_INPUT_KEY]["voice_instructions"]
    parsed = parse_rtc_voice_input(event)
    assert isinstance(parsed, RtcVoiceInputV1)
    assert parsed.voice_instructions is None


def test_blank_or_mistyped_voice_instructions_degrades_to_none():
    for bad in ("", "   ", 42, ["x"], {"a": 1}, None):
        parsed = parse_rtc_voice_input(make_valid_event(voice_instructions=bad))
        assert isinstance(parsed, RtcVoiceInputV1), f"rejected turn for {bad!r}"
        assert parsed.voice_instructions is None


# --- rejection: event shell ----------------------------------------------


def test_wrong_event_type_rejected():
    event = make_valid_event()
    event["type"] = "m.room.encrypted"
    assert parse_rtc_voice_input(event) is None


def test_wrong_msgtype_rejected():
    event = make_valid_event()
    event["content"]["msgtype"] = "m.audio"
    assert parse_rtc_voice_input(event) is None


def test_missing_metadata_rejected():
    event = make_valid_event()
    del event["content"][RTC_VOICE_INPUT_KEY]
    assert parse_rtc_voice_input(event) is None


def test_non_object_metadata_rejected():
    for bad in ("string", 1, ["list"], None, True):
        event = make_valid_event()
        event["content"][RTC_VOICE_INPUT_KEY] = bad
        assert parse_rtc_voice_input(event) is None, f"accepted {bad!r}"


def test_audio_message_with_only_netmind_voice_key_rejected():
    event = make_valid_event()
    del event["content"][RTC_VOICE_INPUT_KEY]
    event["content"]["msgtype"] = "m.audio"
    event["content"]["ai.netmind.voice"] = {"duration_ms": 1200}
    assert parse_rtc_voice_input(event) is None


# --- rejection: core field strictness ------------------------------------


def test_wrong_version_rejected():
    for bad in (2, 0, "1", 1.0, True, None):
        assert parse_rtc_voice_input(make_valid_event(version=bad)) is None, (
            f"accepted version={bad!r}"
        )


def test_wrong_transport_rejected():
    for bad in ("gateway", "", "MATRIX", 1, None):
        assert parse_rtc_voice_input(make_valid_event(transport=bad)) is None, (
            f"accepted transport={bad!r}"
        )


def test_wrong_seq_rejected():
    for bad in (2, 0, "1", 1.5, True, None):
        assert parse_rtc_voice_input(make_valid_event(seq=bad)) is None, (
            f"accepted seq={bad!r}"
        )


def test_non_final_transcript_rejected():
    for bad in (False, "true", 1, None):
        assert parse_rtc_voice_input(
            make_valid_event(transcript_final=bad)
        ) is None, f"accepted transcript_final={bad!r}"


def test_missing_blank_or_mistyped_binding_ids_rejected():
    for field in ("rtc_session_id", "turn_id", "invocation_id", "agent_profile_id"):
        event = make_valid_event()
        del event["content"][RTC_VOICE_INPUT_KEY][field]
        assert parse_rtc_voice_input(event) is None, f"accepted missing {field}"
        for bad in ("", "   ", 7, ["id"], None, True):
            assert parse_rtc_voice_input(
                make_valid_event(**{field: bad})
            ) is None, f"accepted {field}={bad!r}"


# --- envelope splitter ----------------------------------------------------


def test_envelope_split_returns_instructions_and_transcript():
    envelope, transcript = split_narra_system_prompt(ENVELOPE_BODY)
    assert envelope == "Reply for a real-time voice call."
    assert transcript == "What is the weather today?"


def test_body_without_envelope_is_untouched():
    envelope, transcript = split_narra_system_prompt("What is the weather today?")
    assert envelope is None
    assert transcript == "What is the weather today?"


def test_envelope_only_body_yields_empty_transcript():
    body = '<narra-system-prompt version="1" mode="voice">hi</narra-system-prompt>'
    envelope, transcript = split_narra_system_prompt(body)
    assert envelope == "hi"
    assert transcript == ""


def test_unclosed_envelope_is_left_as_transcript():
    body = '<narra-system-prompt version="1" mode="voice">\nWhat is the weather?'
    envelope, transcript = split_narra_system_prompt(body)
    assert envelope is None
    assert transcript == body


def test_envelope_not_at_start_is_left_as_transcript():
    body = "hello <narra-system-prompt>x</narra-system-prompt>"
    envelope, transcript = split_narra_system_prompt(body)
    assert envelope is None
    assert transcript == body


# --- common-trigger extractor ---------------------------------------------


def test_rtc_voice_input_key_pins_the_wire_value():
    # The metadata key is Hybrid's wire contract — a constant rename must
    # never silently change what we look for on the event.
    assert RTC_VOICE_INPUT_KEY == "ai.netmind.rtc.voice_input"


def _event(meta):
    return {
        "type": "m.room.message",
        "content": {"msgtype": "m.text", "body": "hi", RTC_VOICE_INPUT_KEY: meta},
    }


class TestExtractCommonVoiceInstructions:
    def test_broken_metadata_with_instructions_returns_them(self):
        meta = {"version": 1, "seq": 2, "transport": "matrix",
                "transcript_final": True, "voice_instructions": "Reply for a call."}
        assert extract_common_voice_instructions(_event(meta)) == "Reply for a call."

    def test_missing_instructions_returns_none(self):
        assert extract_common_voice_instructions(_event({"seq": 2})) is None

    def test_blank_or_mistyped_instructions_return_none(self):
        assert extract_common_voice_instructions(_event({"voice_instructions": "   "})) is None
        assert extract_common_voice_instructions(_event({"voice_instructions": 7})) is None

    def test_transcript_final_false_carve_out_returns_none(self):
        # An explicit "not final" is a turn-boundary signal, not malformed
        # data: interim STT must never enter degraded voice mode (it would
        # be spoken aloud mid-utterance).
        meta = {"transcript_final": False, "voice_instructions": "Reply for a call."}
        assert extract_common_voice_instructions(_event(meta)) is None

    def test_transcript_final_missing_still_returns_instructions(self):
        meta = {"seq": 2, "voice_instructions": "Reply for a call."}
        assert extract_common_voice_instructions(_event(meta)) == "Reply for a call."

    def test_transcript_final_identity_check_lets_mistyped_values_through(self):
        # The carve-out is `is False` (identity): 0 / "false" are malformed
        # data, not an explicit turn-boundary signal, and keep the common
        # trigger alive.
        for mistyped in (0, "false"):
            meta = {"transcript_final": mistyped,
                    "voice_instructions": "Reply for a call."}
            assert extract_common_voice_instructions(_event(meta)) == (
                "Reply for a call."
            ), f"carve-out swallowed transcript_final={mistyped!r}"

    def test_instructions_capped_at_2000_chars(self):
        meta = {"voice_instructions": "x" * 3000}
        out = extract_common_voice_instructions(_event(meta))
        assert out == "x" * 2000

    def test_metadata_not_a_dict_returns_none(self):
        assert extract_common_voice_instructions(_event("nope")) is None

    def test_non_text_event_returns_none(self):
        ev = _event({"voice_instructions": "x"})
        ev["content"]["msgtype"] = "m.audio"
        assert extract_common_voice_instructions(ev) is None
        assert extract_common_voice_instructions({"type": "m.reaction"}) is None
        assert extract_common_voice_instructions(None) is None
