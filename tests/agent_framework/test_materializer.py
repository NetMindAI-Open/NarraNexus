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
    assemble_argv_prompt,
    flatten_for_argv,
    flatten_for_file,
    split_for_argv,
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


# ---------------- claude two-stage split (agent-loop resume) --------


def test_split_plus_assemble_equals_flatten_byte_identical():
    """The R2/R3 split must compose back to the single-stage function
    byte-for-byte — including the caller-list mutation (one pop each)."""
    m_flat, m_split = _msgs(), _msgs()
    flat_prompt, flat_user = flatten_for_argv(m_flat)
    base, entries, user = split_for_argv(m_split)
    assert user == flat_user
    assert assemble_argv_prompt(base, entries) == flat_prompt
    assert m_split == m_flat  # both paths popped exactly once


def test_split_plus_assemble_byte_identical_under_eviction_and_ceilings():
    def _long_msgs():
        return [
            {"role": "system", "content": "S" * 80},
            {"role": "user", "content": "old-job " + "y" * 40, "_source": "job"},
            {"role": "user", "content": "old-chat " + "汉" * 40, "_source": "chat"},
            {"role": "user", "content": "final"},
        ]

    caps = dict(max_prompt_chars=150, max_prompt_bytes=200, max_history_chars=60)
    flat_prompt, _ = flatten_for_argv(_long_msgs(), **caps)
    base, entries, _ = split_for_argv(_long_msgs())
    assert assemble_argv_prompt(base, entries, **caps) == flat_prompt


def test_split_pops_exactly_once_and_returns_raw_parts():
    messages = _msgs()
    base, entries, user = split_for_argv(messages)
    assert user == "now reply"
    assert len(messages) == 4  # load-bearing mutation, same as flatten
    assert base == "SYS-A\nSYS-B\n"  # bare prompt: no history appended yet
    assert entries == [
        {"role": "user", "content": "hello", "source": "chat"},
        {"role": "assistant", "content": "hi there", "source": "chat"},
    ]


def test_assemble_with_empty_history_omits_history_tail():
    """Resume turns assemble with [] — the prompt is the bare system prompt
    (history lives in the CLI session file), no header/footer appended."""
    base, entries, _ = split_for_argv(_msgs())
    assert entries  # the turn HAS history; the caller chose to omit it
    prompt = assemble_argv_prompt(base, [])
    assert prompt == base
    assert "Chat History" not in prompt


def test_assemble_ceilings_still_apply_to_bare_prompt():
    # Belt-and-braces: a resume-turn system prompt can overrun argv alone.
    prompt = assemble_argv_prompt("S" * 300, [], max_prompt_chars=100)
    assert prompt.endswith("[...truncated due to length limit...]")
    assert prompt.startswith("S" * 100)


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


# ---------------- history honesty (prod 2026-07-29) ------------------
#
# The "how to read the conversation history below" preamble used to be
# glued onto the system prompt by context_runtime, one layer ABOVE the
# eviction decision. When the budget collapsed the materializer dropped
# every history row — but the preamble was already baked in, so the model
# was told "the messages that follow are your recent conversation history,
# each line prefixed [time · topic · nar_id]" and then handed nothing.
# That is worse than no history: it invites the model to invent one.
# The preamble now ships WITH the block it describes.


def test_preamble_ships_with_the_history_block():
    system_prompt, _ = flatten_for_argv(_msgs())
    assert "How to read the conversation history below" in system_prompt
    assert system_prompt.index("How to read the conversation history below") < \
        system_prompt.index("=== Chat History ===")


def test_no_preamble_when_every_history_row_is_evicted():
    """Budget too small for even one row -> no preamble, no dangling guide."""
    messages = [
        {"role": "system", "content": "S" * 200},
        {"role": "user", "content": "hi", "_source": "chat"},
        {"role": "user", "content": "final"},
    ]
    system_prompt, _ = flatten_for_argv(messages, max_prompt_chars=210)
    assert "How to read the conversation history below" not in system_prompt
    assert "[time · topic · nar_id]" not in system_prompt


def test_dropped_history_is_declared_not_silently_omitted():
    """The model must be told the history was withheld, and how many rows."""
    messages = [
        {"role": "system", "content": "S" * 200},
        {"role": "user", "content": "hi", "_source": "chat"},
        {"role": "user", "content": "there", "_source": "chat"},
        {"role": "user", "content": "final"},
    ]
    system_prompt, _ = flatten_for_argv(messages, max_prompt_chars=260)
    assert "omitted this turn" in system_prompt
    assert "2" in system_prompt.split("omitted this turn")[1][:80]


def test_no_notice_when_there_was_never_any_history():
    """A first turn has nothing to declare — stay silent."""
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "final"},
    ]
    system_prompt, _ = flatten_for_argv(messages)
    assert "omitted this turn" not in system_prompt
    assert "Chat History" not in system_prompt


def test_file_strategy_ships_the_same_preamble():
    system_prompt, _ = flatten_for_file(_msgs())
    assert "How to read the conversation history below" in system_prompt
