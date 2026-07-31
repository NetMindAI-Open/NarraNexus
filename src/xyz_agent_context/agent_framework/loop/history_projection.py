"""
@file_name: history_projection.py
@author: Bin Liang
@date: 2026-07-29
@description: Fold a persisted ``events.event_log`` back into provider
chat messages — native turn replay for self-projecting frameworks.

The write side already exists and is driver-neutral: every turn's
``ExecutionState.all_steps`` (tool_call / tool_output / thinking rows,
chronological) is persisted verbatim as ``events.event_log`` by step_4.
This module is the read side: it rebuilds the assistant/tool message
sequence of a PAST turn so a framework that projects its own context
(NexusPower) can hand the model real conversation structure — tool
calls with arguments, paired results, monologue in position — instead
of the two-line flattened summary.

Folding rules (mirroring TurnLedger's projection invariants):
  - consecutive monologue segments + tool calls fold into ONE assistant
    message (content + tool_calls); the first tool_output flushes it;
  - tool messages pair by call id; outputs with no id fall back to the
    oldest unanswered call (parallel calls return in completion order,
    so positional pairing is a last resort, never the default);
  - a flushed call that never got an output is closed with a synthetic
    tool message BEFORE the next assistant message — an assistant
    ``tool_calls`` entry with no following tool message is a provider
    400 on the whole request;
  - orphan outputs (no matching flushed call) are dropped;
  - malformed rows are skipped. This is a projection of historical
    data for context enrichment: a bad row must degrade the replay,
    never break the turn (fail-open, per row).

Claude/codex turns also have event_logs, but their assistant TEXT never
enters ``all_steps`` (it reaches ``final_output`` via append_text), so
folding them yields tool traffic without prose. That is why native
replay is a NexusPower-only feature (Owner decision 2026-07-29 Q1):
only nexus turns carry positioned ``monologue`` segments (stamped by
``ExecutionState.record_thinking``).
"""

from __future__ import annotations

import json
from typing import Any

_SYNTHETIC_MISSING_RESULT = "[no result was recorded for this call]"

#: Frameworks whose driver consumes structured provider messages and can
#: therefore receive native turn replays instead of flattened history.
#: CLI-backed drivers (claude_code, codex_cli) flatten at their doorstep
#: and structurally cannot — see the module docstring.
NATIVE_REPLAY_FRAMEWORKS = frozenset({"nexus_power"})


def fold_event_log_to_messages(entries: list[Any]) -> list[dict[str, Any]]:
    """Fold event_log entries (chronological) into provider messages.

    Accepts both persisted shapes: raw step dicts (``all_steps`` rows)
    and ``EventLogEntry``-shaped wrappers (``{"type", "content": step}``)
    — step_4 wraps each step in an entry whose ``content`` is the step.

    Returns ``[]`` when nothing foldable exists (empty log, text-only
    turn with no monologue, or all rows malformed) — the caller keeps
    the flattened row in that case.
    """
    messages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    unanswered: dict[str, None] = {}  # ordered set of flushed, unpaired ids

    def close_unanswered() -> None:
        for call_id in list(unanswered):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _SYNTHETIC_MISSING_RESULT,
                }
            )
        unanswered.clear()

    def flush_assistant() -> None:
        if not text_parts and not calls:
            return
        close_unanswered()  # the previous batch's pairing closes first
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts) or None,
        }
        if calls:
            message["tool_calls"] = list(calls)
            for call in calls:
                unanswered[call["id"]] = None
        messages.append(message)
        text_parts.clear()
        calls.clear()

    for entry in entries or []:
        step = _unwrap(entry)
        if step is None:
            continue
        step_type = step.get("type")
        if step_type == "thinking":
            monologue = step.get("monologue")
            if isinstance(monologue, str) and monologue:
                text_parts.append(monologue)
        elif step_type == "tool_call":
            name = step.get("tool_name")
            call_id = step.get("tool_call_id")
            if not name or not call_id:
                continue
            arguments = step.get("arguments")
            try:
                rendered = json.dumps(
                    arguments if isinstance(arguments, dict) else {},
                    ensure_ascii=False,
                )
            except (TypeError, ValueError):
                rendered = "{}"
            calls.append(
                {
                    "id": str(call_id),
                    "type": "function",
                    "function": {"name": str(name), "arguments": rendered},
                }
            )
        elif step_type == "tool_output":
            flush_assistant()
            call_id = str(step.get("tool_call_id") or "")
            if call_id and call_id in unanswered:
                del unanswered[call_id]
            elif not call_id and unanswered:
                # Positional fallback: answer the oldest open call.
                call_id = next(iter(unanswered))
                del unanswered[call_id]
            else:
                continue  # orphan output — nothing to pair with
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(step.get("output") or ""),
                }
            )
        # agent_final_output / unknown types: skipped. The final-output
        # step duplicates the monologue cumulatively (position lost);
        # replaying both would double the prose.

    flush_assistant()
    close_unanswered()
    return messages


def _unwrap(entry: Any) -> dict[str, Any] | None:
    """Normalize an event_log row to its inner step dict, or None."""
    if isinstance(entry, dict):
        content = entry.get("content")
        if isinstance(content, dict) and "type" in content:
            return content
        if "type" in entry:
            return entry
        return None
    # Pydantic EventLogEntry (attribute access) — tolerated so callers
    # may pass model objects without dumping first.
    content = getattr(entry, "content", None)
    if isinstance(content, dict) and "type" in content:
        return content
    return None
