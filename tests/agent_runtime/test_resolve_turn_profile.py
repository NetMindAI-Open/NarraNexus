"""
@file_name: test_resolve_turn_profile.py
@date: 2026-08-14
@description: _resolve_turn_profile — trigger-facing fast_mode boolean -> TurnProfile.

Locks:
- Default (fast_mode=False, no profile) resolves to None: today's path,
  byte-identical behavior.
- fast_mode=True builds the profile from working_source (chat -> chat_fast).
- An explicit turn_profile ALWAYS wins over the boolean — paths that build
  their own profile (voice) are unaffected by fast_mode.
"""
from __future__ import annotations

from narranexus.platform.agent_runtime.agent_runtime import _resolve_turn_profile
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.schema.turn_profile import TurnProfile


def test_default_resolves_to_none():
    assert _resolve_turn_profile(False, None, WorkingSource.CHAT) is None


def test_fast_mode_builds_profile_from_working_source():
    p = _resolve_turn_profile(True, None, WorkingSource.CHAT)
    assert p is not None
    assert p.name == "chat_fast"
    assert p.is_fast is True


def test_fast_mode_accepts_bare_string_source():
    p = _resolve_turn_profile(True, None, "chat")
    assert p is not None
    assert p.name == "chat_fast"


def test_explicit_profile_wins_over_fast_mode():
    voice = TurnProfile.voice_fast()
    assert _resolve_turn_profile(True, voice, WorkingSource.CHAT) is voice


def test_explicit_profile_passthrough_without_fast_mode():
    voice = TurnProfile.voice_fast()
    assert _resolve_turn_profile(False, voice, WorkingSource.CHAT) is voice
