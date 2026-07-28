"""
@file_name: test_materializer.py
@date: 2026-07-27
@description: Golden tests for the shared prompt materializer — the two
flatten strategies lifted verbatim from the claude and codex adapters.

These pin the exact observable behavior of the historical inline code:
header/footer bytes, truncation labels, source-aware eviction order,
argv char+byte ceilings (claude) vs generous file budgets (codex), and
— critically — the mutation semantics: the argv strategy POPS the last
message off the caller's list (step_3's fallback later reads that same
list), while the file strategy works on a copy.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.adapters.materializer import (
    flatten_for_argv,
    flatten_for_file,
)

_FOOTER = (
    "\n=== Chat History End ===\n"
    " These are the chat history between you and the user. "
    "This time please make the response by user input in this turn."
)


def _msgs():
    return [
        {"role": "system", "content": "SYS-A"},
        {"role": "system", "content": "SYS-B"},
        {"role": "user", "content": "hello", "_source": "chat"},
        {"role": "assistant", "content": "hi there", "_source": "chat"},
        {"role": "user", "content": "now reply"},
    ]


# ---------------- claude strategy (argv) ----------------------------


def test_argv_basic_shape_and_history_block():
    messages = _msgs()
    system_prompt, user_msg = flatten_for_argv(messages)
    assert user_msg == "now reply"
    assert system_prompt.startswith("SYS-A\nSYS-B\n")
    assert (
        "\n\n=== Chat History ===\nUser: hello\n\nAssistant: hi there" + _FOOTER
        in system_prompt
    )


def test_argv_mutates_caller_list():
    """Load-bearing quirk: the historical inline code popped the last
    message off the CALLER's list; step_3's fallback path reads the
    same list afterwards. The lifted function must keep doing it."""
    messages = _msgs()
    flatten_for_argv(messages)
    assert len(messages) == 4
    assert messages[-1]["content"] == "hi there"


def test_argv_source_aware_eviction_drops_background_first():
    # Budget small enough to force evictions: keep the newest chat rows,
    # drop the OLDEST background row first even though a chat row is older.
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "old-chat " + "x" * 40, "_source": "chat"},
        {"role": "user", "content": "old-job " + "y" * 40, "_source": "job"},
        {"role": "user", "content": "recent " + "z" * 40, "_source": "chat"},
        {"role": "user", "content": "final"},
    ]
    system_prompt, _ = flatten_for_argv(messages, max_history_chars=120)
    assert "old-job" not in system_prompt          # tier 1: background first
    assert "old-chat" in system_prompt             # chat survives while bg exists
    assert "truncated by source-aware eviction" in system_prompt


def test_argv_no_history_when_system_prompt_fills_ceiling():
    messages = [
        {"role": "system", "content": "S" * 200},
        {"role": "user", "content": "hi", "_source": "chat"},
        {"role": "user", "content": "final"},
    ]
    system_prompt, _ = flatten_for_argv(messages, max_prompt_chars=210)
    assert "Chat History" not in system_prompt


def test_argv_char_ceiling_appends_marker():
    messages = [
        {"role": "system", "content": "S" * 300},
        {"role": "user", "content": "final"},
    ]
    system_prompt, _ = flatten_for_argv(messages, max_prompt_chars=100)
    assert system_prompt.endswith("[...truncated due to length limit...]")
    assert system_prompt.startswith("S" * 100)


def test_argv_byte_ceiling_truncates_at_utf8_boundary():
    # 3-byte CJK chars: 100 chars = 300 bytes; a 200-byte ceiling must cut
    # at a valid UTF-8 boundary and append the byte-limit marker.
    messages = [
        {"role": "system", "content": "汉" * 100},
        {"role": "user", "content": "final"},
    ]
    system_prompt, _ = flatten_for_argv(
        messages, max_prompt_chars=10_000, max_prompt_bytes=200
    )
    assert system_prompt.endswith("[...truncated due to byte limit...]")
    head = system_prompt.rsplit("\n\n[...", 1)[0]
    assert head == "汉" * 66  # 200 // 3 = 66 whole chars survive
    head.encode("utf-8")  # must be valid UTF-8


# ---------------- codex strategy (file) -----------------------------


def test_file_basic_shape_matches_argv_body_format():
    messages = _msgs()
    system_prompt, user_msg = flatten_for_file(messages)
    assert user_msg == "now reply"
    assert system_prompt.startswith("SYS-A\nSYS-B\n")
    assert (
        "\n\n=== Chat History ===\nUser: hello\n\nAssistant: hi there" + _FOOTER
        in system_prompt
    )


def test_file_does_not_mutate_caller_list():
    messages = _msgs()
    flatten_for_file(messages)
    assert len(messages) == 5


def test_file_empty_messages_returns_empty_pair():
    assert flatten_for_file([]) == ("", "")


def test_file_no_truncation_label_variant():
    """The codex strategy never relabels the history block — even after
    eviction the header stays '=== Chat History ==='."""
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "a" * 300, "_source": "job"},
        {"role": "user", "content": "b" * 50, "_source": "chat"},
        {"role": "user", "content": "final"},
    ]
    system_prompt, _ = flatten_for_file(messages, max_history_chars=100)
    assert "=== Chat History ===" in system_prompt
    assert "truncated by source-aware eviction" not in system_prompt
    assert "a" * 300 not in system_prompt  # background row evicted first
