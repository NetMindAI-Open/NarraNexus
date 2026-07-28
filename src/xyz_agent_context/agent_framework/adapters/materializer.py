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
  function docstring.
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
ARGV_MAX_PROMPT_CHARS = 115_000
ARGV_MAX_PROMPT_BYTES = 120 * 1024
ARGV_MAX_HISTORY_CHARS = 50_000

# codex/file defaults — instructions.md has no argv limit; budgets exist
# only to keep prompt token cost sane.
FILE_MAX_PROMPT_CHARS = 400_000
FILE_MAX_HISTORY_CHARS = 200_000

_HISTORY_HEADER = "\n\n=== Chat History ===\n"
_HISTORY_FOOTER = (
    "\n=== Chat History End ===\n"
    " These are the chat history between you and the user. "
    "This time please make the response by user input in this turn."
)


def _format_entry(e: dict[str, Any]) -> str:
    label = "User" if e["role"] == "user" else "Assistant"
    return f"{label}: {e['content']}"


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
    design). Behavior contract:

    1. system rows concatenate in order (+"\\n" each).
    2. The LAST message is POPPED off the caller's list as the per-turn
       user message. The mutation is observable by step_3's fallback
       path, which later reads the same list — do not "fix" it to a
       copy without sweeping that consumer.
    3. History budget = min(max_history_chars, ceiling - system - block
       overhead); within budget, evict the OLDEST background-trigger
       row first (``_source`` != "chat"), then the oldest chat row.
    4. Char ceiling then byte ceiling (UTF-8-safe cut) apply to the
       final prompt, each appending its own truncation marker.
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

    # Char budget reserved for history within max_prompt_chars.
    # If system_prompt alone is already near/over the ceiling we send NO
    # history — protecting the system instructions is the priority.
    overhead = len(_HISTORY_HEADER) + len(_HISTORY_FOOTER)
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
        system_prompt += (
            f"\n\n=== {label_tag} ===\n{body}{_HISTORY_FOOTER}"
        )

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
            system_prompt += f"{_HISTORY_HEADER}{body}{_HISTORY_FOOTER}"

    if len(system_prompt) > max_prompt_chars:
        logger.warning(
            f"[materializer] system prompt {len(system_prompt)} chars > "
            f"{max_prompt_chars}; truncating"
        )
        system_prompt = system_prompt[:max_prompt_chars] + (
            "\n\n[...truncated due to length limit...]"
        )

    return system_prompt, this_turn_user_message
