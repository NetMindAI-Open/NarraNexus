"""
@file_name: test_voice_turn_detection.py
@date: 2026-08-06
@description: MatrixTrigger — F28 RTC voice turn detection at parse_event.

Locks:
- A valid RTC voice event parses into a ParsedMessage whose content is
  the TRANSCRIPT (envelope stripped) and whose raw["rtc_voice"] carries
  the four binding IDs + the effective voice instructions
  (degraded=False).
- Instruction precedence: metadata voice_instructions wins; the
  narra-system-prompt envelope text is the fallback carrier.
- Two-level detection (handoff 3.4): invalid metadata WITH a non-blank
  voice_instructions string -> DEGRADED voice turn (degraded=True,
  envelope stripped, all four binding IDs empty — they must never feed
  correlation/serialization/security).
- Invalid metadata WITHOUT usable voice_instructions -> plain text:
  no rtc_voice, body untouched (envelope and all).
- A plain text event without metadata is byte-identical to before.
- _voice_profile_for maps rtc_voice presence to TurnProfile.voice_fast()
  and absence to None (the one-shot override: profile exists only on
  the turn whose event carried valid metadata).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nio import RoomMessageText

from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _voice_profile_for,
)
from narranexus.platform.schema.parsed_message import ParsedMessage

HOMESERVER = "matrix.netmind.chat"
AGENT_MXID = f"@agent-88956f5b:{HOMESERVER}"
SENDER = f"@human-caller:{HOMESERVER}"

ENVELOPE = (
    '<narra-system-prompt version="1" mode="voice">\n'
    "Speak casually.\n"
    "</narra-system-prompt>\n\n"
)
TRANSCRIPT = "What is the weather today?"


def _cred(**o) -> NarramessengerCredential:
    base = dict(
        agent_id="agent_voice1",
        bearer_token="tok",
        matrix_homeserver_url=f"https://{HOMESERVER}",
        matrix_user_id=AGENT_MXID,
        matrix_access_token="syt_fake",
    )
    base.update(o)
    return NarramessengerCredential(**base)


def _rtc_meta(**overrides) -> dict:
    meta = {
        "version": 1,
        "rtc_session_id": "rtc-s1",
        "turn_id": "t1",
        "invocation_id": "inv1",
        "agent_profile_id": "prof1",
        "seq": 1,
        "transcript_final": True,
        "transport": "matrix",
        "voice_instructions": "Reply for a real-time voice call.",
    }
    meta.update(overrides)
    return meta


def _voice_event(*, body: str = ENVELOPE + TRANSCRIPT, meta: Any = "default") -> Any:
    ev = RoomMessageText.__new__(RoomMessageText)
    ev.event_id = "$voice1"
    ev.sender = SENDER
    ev.server_timestamp = 1
    ev.body = body
    content: dict[str, Any] = {"msgtype": "m.text", "body": body}
    if meta == "default":
        content["ai.netmind.rtc.voice_input"] = _rtc_meta()
    elif meta is not None:
        content["ai.netmind.rtc.voice_input"] = meta
    ev.source = {
        "type": "m.room.message",
        "event_id": "$voice1",
        "sender": SENDER,
        "content": content,
    }
    return ev


def _parse(ev) -> ParsedMessage:
    trigger = MatrixTrigger()
    # Voice mode is 1:1-only (finding #3); positive cases need a known DM.
    trigger._room_joined["!call:h"] = {SENDER, "@agent:h"}
    raw = trigger._wrap_event(event=ev, room_id="!call:h", credential=_cred())
    assert raw is not None
    msg = trigger.parse_event(raw)
    assert msg is not None
    return msg


def test_valid_voice_event_strips_envelope_and_tags_rtc():
    msg = _parse(_voice_event())
    assert msg.content == TRANSCRIPT
    rtc = msg.raw["rtc_voice"]
    assert rtc["rtc_session_id"] == "rtc-s1"
    assert rtc["turn_id"] == "t1"
    assert rtc["invocation_id"] == "inv1"
    assert rtc["agent_profile_id"] == "prof1"
    assert rtc["voice_instructions"] == "Reply for a real-time voice call."
    assert rtc["degraded"] is False


def test_envelope_is_fallback_when_metadata_has_no_instructions():
    meta = _rtc_meta()
    del meta["voice_instructions"]
    msg = _parse(_voice_event(meta=meta))
    assert msg.content == TRANSCRIPT
    assert msg.raw["rtc_voice"]["voice_instructions"] == "Speak casually."


def test_invalid_metadata_with_instructions_becomes_degraded_voice_turn():
    """Handoff 3.4 common trigger: a failed metadata block no longer
    cancels voice mode when the backend-controlled voice_instructions
    string is present — the turn degrades, it doesn't drop."""
    msg = _parse(_voice_event(meta=_rtc_meta(seq=2)))
    rtc = msg.raw["rtc_voice"]
    assert rtc["degraded"] is True
    assert rtc["voice_instructions"] == "Reply for a real-time voice call."
    for field in ("rtc_session_id", "turn_id", "invocation_id", "agent_profile_id"):
        assert rtc[field] == ""
    # Voice behavior applies: the envelope IS stripped from the content.
    assert msg.content == TRANSCRIPT


def test_invalid_metadata_without_instructions_stays_plain_text():
    """Neither level fires: strict parse failed AND no usable
    voice_instructions -> plain text, body untouched (envelope kept)."""
    no_instructions = _rtc_meta(seq=2)
    del no_instructions["voice_instructions"]
    for meta in (
        no_instructions,
        _rtc_meta(seq=2, voice_instructions="   "),
        _rtc_meta(seq=2, voice_instructions=42),
    ):
        msg = _parse(_voice_event(meta=meta))
        assert "rtc_voice" not in (msg.raw or {})
        assert msg.content == ENVELOPE + TRANSCRIPT


def test_interim_transcript_final_false_stays_plain_text():
    """transcript_final=False fails the strict parse AND is carved out of
    the common trigger (explicit turn-boundary signal, not malformed
    data): an interim STT fragment never enters degraded voice mode."""
    msg = _parse(_voice_event(meta=_rtc_meta(transcript_final=False)))
    assert "rtc_voice" not in (msg.raw or {})
    assert msg.content == ENVELOPE + TRANSCRIPT


def test_plain_text_unchanged():
    msg = _parse(_voice_event(meta=None, body="just text"))
    assert "rtc_voice" not in msg.raw
    assert msg.content == "just text"


def test_voice_profile_for_maps_presence_to_voice_fast():
    voice_msg = _parse(_voice_event())
    profile = _voice_profile_for(voice_msg)
    assert profile is not None and profile.name == "voice_fast"

    plain_msg = _parse(_voice_event(meta=None, body="hello"))
    assert _voice_profile_for(plain_msg) is None


def test_group_room_never_enters_voice_mode():
    """Review finding #3: RTC metadata carries no source binding, so voice
    mode (and its instruction injection) is restricted to 1:1 rooms —
    matching F13's Agent-Human 1:1 call product shape. A group member
    posting crafted metadata gets a plain text turn."""
    trigger = MatrixTrigger()
    trigger._room_joined["!group:h"] = {SENDER, "@agent:h", "@carol:h"}
    raw = trigger._wrap_event(
        event=_voice_event(), room_id="!group:h", credential=_cred()
    )
    msg = trigger.parse_event(raw)
    assert msg is not None
    assert "rtc_voice" not in msg.raw
    assert msg.content == ENVELOPE + TRANSCRIPT  # body untouched


def test_late_voice_upgrade_recovers_cold_roster_cache():
    """Review finding #17: parse_event's PRIVATE gate reads the cached
    member count — an unseen room parses as GROUP and the FIRST turn of a
    real call would lose voice mode. After _classify's authoritative
    lookup says dm, the late upgrade re-runs the same pure detection."""
    trigger = MatrixTrigger()
    # Cold cache: no member count -> parse_event refuses voice mode.
    raw = trigger._wrap_event(
        event=_voice_event(), room_id="!fresh:h", credential=_cred()
    )
    msg = trigger.parse_event(raw)
    assert "rtc_voice" not in msg.raw

    upgraded = trigger._late_voice_upgrade(msg, authoritative_dm=True)
    assert upgraded.content == TRANSCRIPT
    assert upgraded.raw["rtc_voice"]["rtc_session_id"] == "rtc-s1"
    assert upgraded.raw["rtc_voice"]["degraded"] is False

    # Idempotent on already-upgraded and inert on plain messages.
    assert trigger._late_voice_upgrade(upgraded, authoritative_dm=True) is upgraded
    plain = _parse(_voice_event(meta=None, body="hello"))
    assert trigger._late_voice_upgrade(plain, authoritative_dm=True) is plain

    # The boundary is self-asserting (review #23): without the caller
    # DECLARING an authoritative dm verdict, no upgrade happens.
    assert (
        trigger._late_voice_upgrade(msg, authoritative_dm=False) is msg
    )


def test_late_upgrade_recovers_degraded_turn_on_cold_roster_cache():
    """The late upgrade shares _detect_voice_turn, so the DEGRADED level
    must recover on a cold roster cache too: broken v1 metadata with a
    non-blank voice_instructions parses as plain text (GROUP-conservative
    gate), then the authoritative dm verdict upgrades it to a degraded
    voice turn — empty binding IDs, instructions preserved, envelope
    stripped."""
    trigger = MatrixTrigger()
    # Cold cache: no member count -> parse_event refuses voice mode.
    raw = trigger._wrap_event(
        event=_voice_event(meta=_rtc_meta(seq=2)),
        room_id="!freshdeg:h",
        credential=_cred(),
    )
    msg = trigger.parse_event(raw)
    assert "rtc_voice" not in msg.raw

    upgraded = trigger._late_voice_upgrade(msg, authoritative_dm=True)
    assert upgraded.content == TRANSCRIPT
    rtc = upgraded.raw["rtc_voice"]
    assert rtc["degraded"] is True
    assert rtc["voice_instructions"] == "Reply for a real-time voice call."
    for field in ("rtc_session_id", "turn_id", "invocation_id", "agent_profile_id"):
        assert rtc[field] == ""


def test_late_upgrade_drops_envelope_only_turn_like_parse_does():
    """Review finding #21: metadata-valid but envelope-only body must be
    DROPPED on the late path too — never run the agent with the envelope
    as user content (the exact promotion split_narra_system_prompt
    exists to prevent)."""
    trigger = MatrixTrigger()
    raw = trigger._wrap_event(
        event=_voice_event(body=ENVELOPE), room_id="!fresh2:h", credential=_cred()
    )
    msg = trigger.parse_event(raw)  # cold cache -> plain text, not dropped
    assert msg is not None and "rtc_voice" not in msg.raw
    assert trigger._late_voice_upgrade(msg, authoritative_dm=True) is None


@pytest.mark.asyncio
async def test_late_upgrade_wired_only_on_dm_classify(monkeypatch):
    """Review finding #24: the dm-only wiring must be falsifiable — moving
    the _late_voice_upgrade call out of the dm branch must break a test,
    or the 1:1 injection boundary silently reopens."""
    import pytest as _pytest
    from unittest.mock import AsyncMock, MagicMock

    from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase

    for target, expect_called in (("dm", True), ("group_mention", False)):
        trigger = MatrixTrigger()
        spy = MagicMock(side_effect=lambda m, **kw: m)
        monkeypatch.setattr(trigger, "_late_voice_upgrade", spy, raising=False)
        monkeypatch.setattr(trigger, "_is_mentioning_us", lambda *a, **k: True, raising=False)
        monkeypatch.setattr(trigger, "_classify", AsyncMock(return_value=target), raising=False)
        monkeypatch.setattr(
            trigger,
            "_authorize_event",
            AsyncMock(return_value=SimpleNamespace(allow=True, notice_send=False, notice_text="")),
            raising=False,
        )
        monkeypatch.setattr(ChannelTriggerBase, "_process_message", AsyncMock())
        msg = _parse(_voice_event())
        # Pre-classify gates: an active client must exist and the message
        # must not be an echo — stub both so the run reaches classify.
        monkeypatch.setattr(trigger, "_subscriber_key", lambda c: "k", raising=False)
        monkeypatch.setattr(trigger, "_clients", {"k": object()}, raising=False)
        monkeypatch.setattr(trigger, "is_echo", AsyncMock(return_value=False), raising=False)
        try:
            await trigger._process_message(_cred(), msg)
        except Exception as e:  # noqa: BLE001 — wiring assertion is what matters
            _pytest.fail(f"_process_message raised for target={target}: {e}")
        assert spy.called is expect_called, f"target={target}"
