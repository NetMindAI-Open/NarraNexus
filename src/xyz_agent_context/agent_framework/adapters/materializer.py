"""
@file_name: materializer.py
@author: Bin Liang
@date: 2026-07-27
@description: Shared prompt materializer — the "flatten structured
messages into system-prompt text + per-turn user message" step both CLI
adapters perform.

Both the claude and codex drivers receive structured role messages from
context_runtime and destroy the structure at their doorstep, because
each CLI only accepts "one system prompt + one user input". That
materialization used to live as two parallel private implementations
(inline in ``adapters/claude/sdk.py``; ``_build_system_prompt_and_user_msg``
in ``adapters/codex/cli_sdk.py``). This module hosts BOTH strategies
side by side so the materialization step is one explicit, shared seam:

- ``flatten_for_argv``  — claude strategy: argv-delivered prompt, hard
  char AND byte ceilings (Linux MAX_ARG_STRLEN), history budget with
  source-aware eviction, truncation labels. **Mutates the caller's
  list** (pops the final user message) — a load-bearing quirk, see the
  function docstring. Internally two stages (agent-loop resume R2/R3):
  ``split_for_argv`` (the pop + system/history split, exactly once per
  turn) and ``assemble_argv_prompt`` (budget/eviction/ceilings). The
  claude adapter drives the stages itself so a resume turn can assemble
  with an empty history and the stale-handle cold retry can re-assemble
  with the preserved entries; composed, they are byte-identical to the
  single-stage function.
- ``flatten_for_file``  — codex strategy: file-delivered prompt
  (``instructions.md``), no byte ceiling, generous char budgets, same
  source-aware eviction; operates on a copy.

They are deliberately NOT unified into one function: their observable
outputs differ (labels, ceilings, mutation semantics) and byte-level
equivalence with the historical behavior is the contract. A driver that
projects context itself (a self-owned loop) simply never calls this
module — that is the "consumption depth is free" half of the driver
contract.

`_source` on each history row is set by
``context_runtime.build_input_for_framework`` from the row's
``meta_data.working_source``; unknown rows default to "chat". Rows are
never deleted from the database — eviction only governs what this turn
sends to the LLM.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# claude/argv defaults — see the long rationale comment in
# adapters/claude/sdk.py @ agent_loop (argv MAX_ARG_STRLEN 128 KiB;
# 115K chars measured against real module_instructions load 2026-07-03).
#
# STILL LOAD-BEARING, despite the main path no longer using them (2026-07-29).
# Since the claude adapter authors the CLI's resume transcript itself, history
# normally travels in that file and never reaches argv — so the history budget
# and its source-aware eviction look dead. They are not: writing the transcript
# is fail-open, and when it cannot be written the adapter falls back to folding
# history into the argv prompt exactly as before. These ceilings are what keeps
# that fallback from overrunning MAX_ARG_STRLEN.
#
# Recorded because "the main path stopped using it" is a tempting reason to
# delete a limit, and the failure it would reintroduce only shows up on the rare
# path (read-only config dir, full disk).
ARGV_MAX_PROMPT_CHARS = 115_000
ARGV_MAX_PROMPT_BYTES = 120 * 1024
ARGV_MAX_HISTORY_CHARS = 50_000

# codex/file defaults — instructions.md has no argv limit; budgets exist
# only to keep prompt token cost sane.
FILE_MAX_PROMPT_CHARS = 400_000
FILE_MAX_HISTORY_CHARS = 200_000

_HISTORY_FOOTER = (
    "\n=== Chat History End ===\n"
    " These are the chat history between you and the user. "
    "This time please make the response by user input in this turn."
)

# Reading guide for the history block. This lives HERE, next to the code
# that decides whether the block is emitted at all, and not in
# context_runtime where it used to be glued onto the system prompt one
# layer above the eviction decision. Under budget pressure the two
# disagreed: the rows were dropped but the guide was already baked in, so
# the model was told "the messages that follow are your recent
# conversation history" and then handed nothing — an instruction to
# recall something that isn't there (prod 2026-07-29, agent_94360f6c4b98,
# 0 of 30 rows survived on 7 of 10 turns). The guide now ships with the
# block it describes, or not at all.
CHAT_HISTORY_TIMELINE_PREAMBLE = """## How to read the conversation history below

The messages that follow are your recent conversation history with this user,
assembled as a SINGLE timeline ordered by real time. It is built from:
- ALL of the current conversation thread (the narrative you are in now), plus
- the most recent messages from this user's OTHER threads with you,
merged by timestamp and trimmed to roughly the latest 30 lines. Trimmed older
lines are NOT lost — they still live in their narrative (you can pull a full
thread with your narrative tools).

Each line is prefixed:  [<time> · <topic> · <narrative_id>]
- <time>: when it was said. Use it to judge what the user is replying to — a
  short reply ("好" / "ok" / "yes" / "继续") almost always answers the MOST
  RECENT line, i.e. the one just above the current input — NOT an older line
  from a different thread.
- <topic>: a human-readable name of that conversation thread.
- <narrative_id>: the stable id of that thread. Different ids = different
  topics. The current input belongs, by default, to the most recent thread; if
  it really belongs to another thread (or to a brand-new topic), use your
  narrative tools to switch / create.

Visibility: in each past turn the user only saw the message you SENT to them
(the <reply_to_user> part). Your <my_reasoning> was private — do not assume the
user knows anything that only appeared in your reasoning.
"""


def _history_block(body: str, label_tag: str = "Chat History") -> str:
    """Render the reading guide + the history rows as one inseparable unit."""
    return (
        f"\n\n{CHAT_HISTORY_TIMELINE_PREAMBLE}\n"
        f"=== {label_tag} ===\n{body}{_HISTORY_FOOTER}"
    )


def _history_omitted_notice(dropped: int) -> str:
    """Say out loud that history existed and was withheld.

    Silence is not neutral here: with no marker the model reads the turn as
    a fresh conversation and confidently re-derives context it should have
    asked for. Naming the gap — and pointing at the tools that can close
    it — is the difference between "I don't know yet" and a fabrication.
    """
    return (
        "\n\n=== Chat History ===\n"
        f"(omitted this turn — {dropped} earlier message(s) did not fit this "
        "turn's prompt budget. This is NOT a fresh conversation: assume prior "
        "context exists that you cannot see. Do not guess what was said; if "
        "this turn depends on it, retrieve it with your narrative / memory "
        "tools or ask the user.)"
        "\n=== Chat History End ===\n"
    )


def _format_entry(e: dict[str, Any]) -> str:
    label = "User" if e["role"] == "user" else "Assistant"
    return f"{label}: {e['content']}"


def split_for_argv(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], str]:
    """Claude-strategy stage 1: split ``messages`` into
    ``(base_system_prompt, history_entries, this_turn_user_message)``.

    Split out of ``flatten_for_argv`` (2026-07-28, agent-loop resume R2/R3)
    so the claude adapter can decide PER RUN whether the history entries go
    into the prompt: a resume turn assembles with ``[]`` (history lives in
    the CLI session file), and the stale-handle cold retry re-assembles with
    the SAME preserved entries — without re-splitting (the pop already
    happened exactly once).

    Behavior contract (unchanged from the historical inline code):

    1. system rows concatenate in order (+"\\n" each).
    2. The LAST message is POPPED off the caller's list as the per-turn
       user message. The mutation is observable by step_3's fallback
       path, which later reads the same list — do not "fix" it to a
       copy without sweeping that consumer. The pop happens exactly
       ONCE per turn, here.
    """
    system_prompt = ""
    history_entries: list[dict[str, Any]] = []  # ordered oldest -> newest
    this_turn_user_message = (messages.pop())["content"]    # TODO: Not robust enough; if the last message is not a user message, a logic error will occur. Needs adjustment.
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_prompt += msg["content"] + "\n"
        elif role in ("user", "assistant"):
            history_entries.append({
                "role": role,
                "content": msg.get("content", ""),
                "source": msg.get("_source", "chat"),
            })
    return system_prompt, history_entries, this_turn_user_message


def assemble_argv_prompt(
    base_system_prompt: str,
    history_entries: list[dict[str, Any]],
    *,
    max_prompt_chars: int = ARGV_MAX_PROMPT_CHARS,
    max_prompt_bytes: int = ARGV_MAX_PROMPT_BYTES,
    max_history_chars: int = ARGV_MAX_HISTORY_CHARS,
) -> str:
    """Claude-strategy stage 2: append history within budget and enforce
    the argv char + UTF-8-byte ceilings.

    Composed with ``split_for_argv`` this is byte-identical to the
    historical single-stage ``flatten_for_argv``. Behavior contract:

    1. History budget = min(max_history_chars, ceiling - system - block
       overhead); within budget, evict the OLDEST background-trigger
       row first (``source`` != "chat"), then the oldest chat row.
    2. Char ceiling then byte ceiling (UTF-8-safe cut) apply to the
       final prompt, each appending its own truncation marker.

    Resume turns call this with EMPTY ``history_entries`` (the history
    lives in the CLI session file) — the ceilings still apply to the bare
    system prompt (belt-and-braces: it can overrun argv on its own).
    """
    system_prompt = base_system_prompt

    # Char budget reserved for history within max_prompt_chars.
    # If system_prompt alone is already near/over the ceiling we send NO
    # history — protecting the system instructions is the priority.
    overhead = len(_history_block(""))
    sys_len = len(system_prompt)
    history_budget = max(
        0,
        min(max_history_chars, max_prompt_chars - sys_len - overhead),
    )

    kept: list[dict[str, Any]] = []
    if history_entries and history_budget > 0:
        kept = list(history_entries)

        def _join_len(rows: list[dict[str, Any]]) -> int:
            if not rows:
                return 0
            # +2 per separator "\n\n" between rows
            return sum(len(_format_entry(r)) for r in rows) + 2 * (len(rows) - 1)

        dropped_bg = 0
        dropped_chat = 0
        while kept and _join_len(kept) > history_budget:
            # Tier 1: drop the oldest non-chat row.
            bg_idx = next(
                (i for i, r in enumerate(kept) if r["source"] != "chat"),
                None,
            )
            if bg_idx is not None:
                kept.pop(bg_idx)
                dropped_bg += 1
            else:
                # Tier 2: drop the oldest chat row.
                kept.pop(0)
                dropped_chat += 1

        if dropped_bg or dropped_chat:
            logger.warning(
                f"History truncated by source-aware eviction: "
                f"dropped {dropped_bg} background-trigger rows "
                f"+ {dropped_chat} chat rows, kept {len(kept)} of "
                f"{len(history_entries)} (budget {history_budget} chars)."
            )
    elif history_entries:
        logger.warning(
            f"System prompt alone ({sys_len} chars) leaves no room for "
            f"history; omitting all {len(history_entries)} history rows."
        )

    if kept:
        body = "\n\n".join(_format_entry(r) for r in kept)
        label_tag = (
            "Chat History"
            if len(kept) == len(history_entries)
            else "Chat History (truncated by source-aware eviction)"
        )
        system_prompt += _history_block(body, label_tag)
    elif history_entries:
        # Rows existed and none survived. Declare the gap instead of
        # letting the turn look like a fresh conversation.
        system_prompt += _history_omitted_notice(len(history_entries))

    # Belt-and-braces (rare now): char + byte caps still apply because
    # multi-byte content blows past char budget in the worst case, and
    # the system_prompt itself might exceed max_prompt_chars
    # (in which case the eviction loop already gave us 0-budget history,
    # but the system prompt still needs to fit argv).
    if len(system_prompt) > max_prompt_chars:
        logger.warning(
            f"System prompt still too long after source-aware eviction "
            f"({len(system_prompt)} chars > {max_prompt_chars}), "
            f"hard-truncating to char ceiling"
        )
        system_prompt = system_prompt[:max_prompt_chars] + "\n\n[...truncated due to length limit...]"

    _encoded = system_prompt.encode("utf-8")
    if len(_encoded) > max_prompt_bytes:
        logger.warning(
            f"System prompt exceeds byte ceiling "
            f"({len(_encoded)} bytes > {max_prompt_bytes}), "
            f"truncating at UTF-8 boundary"
        )
        # decode('utf-8', errors='ignore') drops any partial multi-byte
        # sequence introduced by the byte slice, so the result is always
        # valid UTF-8.
        system_prompt = _encoded[:max_prompt_bytes].decode("utf-8", errors="ignore")
        system_prompt += "\n\n[...truncated due to byte limit...]"

    return system_prompt


def flatten_for_argv(
    messages: list[dict[str, Any]],
    *,
    max_prompt_chars: int = ARGV_MAX_PROMPT_CHARS,
    max_prompt_bytes: int = ARGV_MAX_PROMPT_BYTES,
    max_history_chars: int = ARGV_MAX_HISTORY_CHARS,
) -> tuple[str, str]:
    """Claude-strategy flatten: (system_prompt, this_turn_user_message).

    Lifted verbatim from the inline block in
    ``ClaudeAgentSDK.agent_loop`` (2026-05-19 source-aware truncation
    design), now the trivial composition of ``split_for_argv`` (pops the
    caller's last message — load-bearing, see its docstring) and
    ``assemble_argv_prompt`` (budget/eviction/ceilings). History ALWAYS
    folds into the prompt on this path; a caller that needs the
    resume-aware split drives the two stages itself (the claude adapter).
    """
    base_system_prompt, history_entries, this_turn_user_message = (
        split_for_argv(messages)
    )
    system_prompt = assemble_argv_prompt(
        base_system_prompt,
        history_entries,
        max_prompt_chars=max_prompt_chars,
        max_prompt_bytes=max_prompt_bytes,
        max_history_chars=max_history_chars,
    )
    return system_prompt, this_turn_user_message


def flatten_for_file(
    messages: list[dict[str, Any]],
    *,
    max_prompt_chars: int = FILE_MAX_PROMPT_CHARS,
    max_history_chars: int = FILE_MAX_HISTORY_CHARS,
) -> tuple[str, str]:
    """Codex-strategy flatten: (system_prompt, this_turn_user_message).

    Lifted verbatim from ``cli_sdk._build_system_prompt_and_user_msg``.
    Lighter-weight than the argv strategy because codex reads
    instructions from a file (no argv length limit): no byte ceiling,
    no truncation relabel, and it operates on a COPY of ``messages``
    (the caller's list is never mutated).
    """
    if not messages:
        return "", ""

    # Last entry is the per-turn user message — same convention as CC.
    messages = list(messages)
    this_turn_user_message = (messages.pop()).get("content", "") or ""

    system_prompt = ""
    history_entries: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_prompt += (msg.get("content") or "") + "\n"
        elif role in ("user", "assistant"):
            history_entries.append({
                "role": role,
                "content": msg.get("content") or "",
                "source": msg.get("_source", "chat"),
            })

    if history_entries:
        original_rows = len(history_entries)
        body = "\n\n".join(_format_entry(r) for r in history_entries)
        # Source-aware eviction: drop oldest non-chat messages first if
        # body exceeds budget. Same priority as the argv strategy.
        while len(body) > max_history_chars and history_entries:
            bg_idx = next(
                (i for i, r in enumerate(history_entries) if r["source"] != "chat"),
                None,
            )
            if bg_idx is not None:
                history_entries.pop(bg_idx)
            else:
                history_entries.pop(0)
            body = "\n\n".join(_format_entry(r) for r in history_entries)

        if history_entries:
            system_prompt += _history_block(body)
        else:
            system_prompt += _history_omitted_notice(original_rows)

    if len(system_prompt) > max_prompt_chars:
        logger.warning(
            f"[materializer] system prompt {len(system_prompt)} chars > "
            f"{max_prompt_chars}; truncating"
        )
        system_prompt = system_prompt[:max_prompt_chars] + (
            "\n\n[...truncated due to length limit...]"
        )

    return system_prompt, this_turn_user_message
