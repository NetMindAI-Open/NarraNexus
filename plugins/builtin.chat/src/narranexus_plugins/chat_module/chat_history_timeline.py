"""
@file_name: chat_history_timeline.py
@author: NarraNexus
@date: 2026-08-30
@description: Pure event_log -> timeline projection for builtin.chat's event-log route.

Lifted out of chat_history.py, which is well past the 800-line convention.
Everything here is a pure function over already-loaded rows: no DB, no request
context, so `tests/backend/test_event_log_monologue_tier.py` can feed it step
dicts directly instead of standing up a database.

Lives in the plugin package (batch 6b) rather than under `backend/routes/`:
its ONE caller is `routes.py` in this package, and it is chat business logic,
not a host service — importing it from `backend.routes.agents` was the last
`backend.*` import in the chat plugin, and a private one at that.
"""

from typing import Any, Dict, List

from narranexus.platform.schema.api_schema import EventLogTimelineEntry


def is_monologue_step(entry: Dict[str, Any]) -> bool:
    """Is this persisted thinking step tier-PURE NexusPower monologue?

    ``execution_state.record_thinking`` stores ``content`` (the coalesced
    DISPLAY stream) alongside ``monologue`` (the monologue SUBSET of it).

    A step where the subset is only PART of the content is unsplittable — the
    union and the subset are recorded, the positions are not. Since 2026-08-30
    the batcher flushes on a tier switch, so such a step can only come from a
    row persisted before that; the equality test stays as the guard that keeps
    the failure pointing the safe way if mixing is ever reintroduced. It
    reports False and renders as ordinary thinking: the failure that hides
    narration among CoT, never the one that promotes provider scratchpad or
    invents a boundary that was not recorded.
    """
    monologue = entry.get("monologue") or ""
    return bool(monologue) and monologue == (entry.get("content") or "")


def build_event_timeline(
    entries_content: List[Any],
    call_names: Dict[str, str],
) -> List[EventLogTimelineEntry]:
    """Walk stored event_log entries into the time-ordered timeline view.

    Their original order IS the agent's actual think→tool→think→tool→reply
    rhythm, which the grouped thinking/tool_calls fields lose. Consecutive
    thinking deltas are concatenated so the UI does not render 50 tiny italic
    blocks — but only within one tier: a monologue/CoT switch is a block
    boundary, because one entry carries one tier flag and a merged block would
    label two tiers of text with one of them (see ``is_monologue_step``).

    ``call_names`` is the shared tool_call_id → name index built by the caller
    and read by both this view and the grouped tool_calls view.

    The tier carried here is what lets a RELOADED (ended) turn look like the
    live one. A refresh taken mid-run goes through run_recorder/broadcaster
    instead, which carry it too since 2026-08-30 — all three replay paths now
    agree.
    """
    timeline: List[EventLogTimelineEntry] = []
    pending_thinking: List[str] = []
    pending_is_monologue = False
    # Stored tool_output entries usually carry no tool_name of their own.
    # Resolve via the SHARED call_names index (parallel calls interleave:
    # every call lands before any output, so "nearest preceding call" would
    # confidently attach the WRONG name); nearest-preceding survives only as
    # the fallback for legacy rows without ids. Never invent a placeholder
    # ("unknown") — and the write side (response_processor's record_tool_call
    # persistence) honours the same rule, so new rows never carry one either;
    # the frontend's normalization is a shim for rows persisted before.
    last_tool_name = ""

    def _flush_thinking():
        nonlocal pending_is_monologue
        if pending_thinking:
            timeline.append(EventLogTimelineEntry(
                type="thinking",
                content="".join(pending_thinking),
                monologue=pending_is_monologue,
            ))
            pending_thinking.clear()
            pending_is_monologue = False

    for content in entries_content:
        if not isinstance(content, dict):
            continue
        ctype = content.get("type")
        if ctype == "thinking":
            txt = content.get("content", "")
            if txt:
                tier = is_monologue_step(content)
                if pending_thinking and tier != pending_is_monologue:
                    _flush_thinking()
                pending_is_monologue = tier
                pending_thinking.append(txt)
        elif ctype == "tool_call":
            _flush_thinking()
            # Some legacy stored entries carry a reply_via tag on the
            # send_message tool — preserve it so the historical Reply
            # block can render the "helper_llm fallback" badge.
            last_tool_name = content.get("tool_name") or ""
            timeline.append(EventLogTimelineEntry(
                type="tool_call",
                tool_name=last_tool_name,
                tool_input=content.get("arguments", {}) or {},
                reply_via=(content.get("details") or {}).get("reply_via"),
            ))
        elif ctype == "tool_output":
            _flush_thinking()
            out_id = content.get("tool_call_id") or ""
            if content.get("tool_name"):
                out_name = content.get("tool_name")
            elif out_id in call_names:
                # Membership check, not an `or` chain: a known-empty name
                # must stay empty rather than fall through to the nearest
                # sibling's name.
                out_name = call_names[out_id]
            elif out_id:
                # An id we never saw a call for: we OUGHT to know the owner
                # and genuinely don't — an honest blank beats a sibling's
                # name. last_tool_name serves id-less legacy rows only.
                out_name = ""
            else:
                out_name = last_tool_name
            timeline.append(EventLogTimelineEntry(
                type="tool_output",
                tool_name=out_name,
                tool_output=content.get("output"),
            ))
        elif ctype in ("native_output", "agent_response"):
            _flush_thinking()
            txt = content.get("content", "")
            if txt:
                timeline.append(EventLogTimelineEntry(
                    type="native_output",
                    content=txt,
                ))
        # Other types (progress markers, etc.) intentionally skipped.
    _flush_thinking()
    return timeline
