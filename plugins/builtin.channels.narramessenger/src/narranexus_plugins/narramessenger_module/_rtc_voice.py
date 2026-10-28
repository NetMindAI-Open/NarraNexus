"""
@file_name: _rtc_voice.py
@date: 2026-08-06
@description: F28 RTC voice-input metadata — v1 parser + body envelope splitter.

Contract source: Hybrid "Direct Matrix RTC fast reply" handoff, sections 3.1/3.2/4.2.
The metadata is a performance / presentation hint, NOT an authorization
credential: callers must keep every existing sender / room check in place.

Two-level trigger contract: a strictly-validated v1 metadata block
(parse_rtc_voice_input) starts a full voice turn with correlation IDs
(rtc_session_id/turn_id/invocation_id/agent_profile_id). When that strict
parse fails but the metadata still carries a non-blank `voice_instructions`
string (extract_common_voice_instructions), the event starts a degraded
voice turn — voice mode without correlation. When neither is present, the
event is plain text. In every case, nothing here may ever break the normal
reply path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Tuple

RTC_VOICE_INPUT_KEY = "ai.netmind.rtc.voice_input"

# Cap for instructions accepted through the degraded common trigger, where
# nothing else bounds the string (the strict parser's contract does).
COMMON_INSTRUCTIONS_MAX_CHARS = 2000

_BINDING_ID_FIELDS = (
    "rtc_session_id",
    "turn_id",
    "invocation_id",
    "agent_profile_id",
)

# Envelope must sit at the very start of the body (leading whitespace aside);
# anywhere else it is user-authored text, not a delivery envelope.
_ENVELOPE_RE = re.compile(
    r"\A\s*<narra-system-prompt\b[^>]*>(.*?)</narra-system-prompt>\s*",
    re.DOTALL,
)


@dataclass(frozen=True)
class RtcVoiceInputV1:
    rtc_session_id: str
    turn_id: str
    invocation_id: str
    agent_profile_id: str
    voice_instructions: Optional[str] = None


def _is_strict_int(value: Any, expected: int) -> bool:
    # bool is an int subclass in Python; the contract's `=== 1` excludes it.
    return type(value) is int and value == expected


def _non_blank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _voice_metadata(event: Any) -> Optional[dict]:
    """Shared event-shell guard: the RTC metadata dict, or None unless the
    event is an m.text room message whose metadata object is a dict."""
    if not isinstance(event, dict) or event.get("type") != "m.room.message":
        return None
    content = event.get("content")
    if not isinstance(content, dict) or content.get("msgtype") != "m.text":
        return None
    meta = content.get(RTC_VOICE_INPUT_KEY)
    return meta if isinstance(meta, dict) else None


def parse_rtc_voice_input(event: Any) -> Optional[RtcVoiceInputV1]:
    """Return the validated v1 payload, or None to mean "normal text message".

    Implements every §3.2 rule strictly; voice_instructions is the only
    forgiving field (missing / blank / mistyped -> None, turn stays valid).
    """
    meta = _voice_metadata(event)
    if meta is None:
        return None
    if not _is_strict_int(meta.get("version"), 1):
        return None
    if meta.get("transport") != "matrix":
        return None
    if not _is_strict_int(meta.get("seq"), 1):
        return None
    if meta.get("transcript_final") is not True:
        return None
    if not all(_non_blank_str(meta.get(field)) for field in _BINDING_ID_FIELDS):
        return None

    instructions = meta.get("voice_instructions")
    if not _non_blank_str(instructions):
        instructions = None

    return RtcVoiceInputV1(
        rtc_session_id=meta["rtc_session_id"],
        turn_id=meta["turn_id"],
        invocation_id=meta["invocation_id"],
        agent_profile_id=meta["agent_profile_id"],
        voice_instructions=instructions,
    )


def extract_common_voice_instructions(event: Any) -> Optional[str]:
    """Handoff §3.1/§3.4 common trigger: the backend-controlled, non-blank
    ``voice_instructions`` string inside the (possibly INVALID) v1 metadata
    object. Used only after ``parse_rtc_voice_input`` returned None: a failed
    metadata block no longer cancels voice mode, it only forfeits
    correlation. Returns None unless the event is an m.text room message
    whose metadata is a dict carrying a non-blank string field — the body
    envelope is deliberately NOT consulted (mode detection must not depend
    on body-string parsing).

    Carve-out — ``transcript_final is False``: the backend explicitly
    saying "not final" is a turn-boundary signal, not malformed data, so
    an interim STT fragment must NOT enter degraded voice mode (it would
    be spoken aloud mid-utterance). The check is identity (``is False``)
    on purpose: a missing or mistyped value (including ``0`` / ``"false"``)
    is malformed data and still falls through to the common trigger.

    The returned instructions are capped at
    ``COMMON_INSTRUCTIONS_MAX_CHARS``: this path already lowered the
    construction bar (no strict validation), so an unbounded string must
    not be allowed to inflate the prompt.
    """
    meta = _voice_metadata(event)
    if meta is None:
        return None
    if meta.get("transcript_final") is False:
        return None
    instructions = meta.get("voice_instructions")
    if not _non_blank_str(instructions):
        return None
    return instructions[:COMMON_INSTRUCTIONS_MAX_CHARS]


def split_narra_system_prompt(body: str) -> Tuple[Optional[str], str]:
    """Split the <narra-system-prompt> envelope off the transcript.

    Returns (envelope_text, transcript). A malformed, unclosed, or
    mid-body envelope is NOT stripped: the whole body stays transcript,
    so raw user text can never be promoted to instruction content.
    """
    match = _ENVELOPE_RE.match(body)
    if match is None:
        return None, body
    return match.group(1).strip(), body[match.end():]
