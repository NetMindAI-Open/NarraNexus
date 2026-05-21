"""
@file_name: test_invite_code_gen.py
@author: NarraNexus
@date: 2026-05-14
@description: TDD tests for the invite code generator.

Asserts format, alphabet legality (no ambiguous chars), and practical
distinctness across a large batch.
"""
from __future__ import annotations

from xyz_agent_context.utils.invite_code_gen import (
    INVITE_CODE_ALPHABET,
    INVITE_CODE_BODY_LEN,
    INVITE_CODE_PREFIX,
    generate_code,
)


def test_format_is_prefix_plus_body():
    code = generate_code()
    assert code.startswith(INVITE_CODE_PREFIX)
    body = code[len(INVITE_CODE_PREFIX):]
    assert len(body) == INVITE_CODE_BODY_LEN
    assert len(code) == len(INVITE_CODE_PREFIX) + INVITE_CODE_BODY_LEN


def test_body_uses_only_safe_alphabet():
    for _ in range(500):
        body = generate_code()[len(INVITE_CODE_PREFIX):]
        assert all(ch in INVITE_CODE_ALPHABET for ch in body)


def test_alphabet_excludes_ambiguous_characters():
    for ambiguous in "01ILOU":
        assert ambiguous not in INVITE_CODE_ALPHABET


def test_codes_are_practically_distinct():
    codes = {generate_code() for _ in range(2000)}
    # 2000 draws from a ~2^39 keyspace: a collision here would be a bug,
    # not bad luck.
    assert len(codes) == 2000
