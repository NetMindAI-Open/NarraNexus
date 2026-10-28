"""
@file_name: test_updater_context_sections.py
@date: 2026-08-26
@description: C3 pin — every line the updater context renders is on a closed
              prefix list.

The action-digest section was removed at a9260baa4^.. after it was measured
renaming continuity anchors (same-line rate 65.3% -> 56.8% on anchor-rewritten
rows, McNemar p=0.0002; replay study PR2_CROWDING_ANALYSIS, 2026-08-26 —
headline numbers restated in the updater.py tombstone). Pinning only section
HEADINGS would go green for a new input appended as a bare line under an
existing heading (exactly how `User Input:` / `Agent Response:` already sit
under "## Latest Conversation"), so this pins the prefix of EVERY non-empty
rendered line: any new `context_parts.append(...)` — heading or bare line —
must change the list below and justify itself against C3, because everything
in this context flows through _apply_llm_update's unconditional overwrite of
the fields continuity reads as the anchor.
"""
import re
from datetime import datetime, timezone

import pytest

from narranexus.platform.narrative._narrative_impl import updater as updater_mod
from narranexus.platform.narrative.models import (
    DynamicSummaryEntry,
    Event,
    EventLogEntry,
    Narrative,
    NarrativeInfo,
    NarrativeType,
)

_NOW = datetime.now(timezone.utc)


def _narrative() -> Narrative:
    return Narrative(
        id="nar_c3pin000",
        type=NarrativeType.CHAT,
        agent_id="agent_test",
        narrative_info=NarrativeInfo(
            name="Group-chat self-introduction thread",
            description="",
            current_summary="Onboarding thread; user introduced themselves to the group.",
            actors=[],
        ),
        event_ids=["evt_prev0001"],
        topic_keywords=["onboarding"],
        dynamic_summary=[
            DynamicSummaryEntry(
                event_id="evt_prev0001",
                summary="User introduced themselves to the group.",
                timestamp=_NOW,
            ),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _event() -> Event:
    return Event(
        id="evt_c3pin000", trigger="chat", trigger_source="user_x",
        env_context={"input": "did anyone reply to my intro?"},
        module_instances=[],
        event_log=[
            EventLogEntry(
                timestamp=_NOW, type="tool_call",
                content={"tool_name": "Read", "arguments": {"file_path": "/tmp/web.log"}},
            ),
        ],
        final_output="Done — findings sent.",
        created_at=_NOW, updated_at=_NOW,
        agent_id="agent_test", user_id="user_x",
    )


def _line_prefix(line: str) -> str:
    """Normalize one rendered line to its stable prefix.

    Numbered dynamic-summary lines collapse to "<n>." so the pin does not
    depend on NARRATIVE_LLM_UPDATE_EVENTS_COUNT; "Key: value" lines keep the
    key; headings keep themselves.
    """
    if re.match(r"^\d+\. ", line):
        return "<n>."
    return line.split(":")[0]


@pytest.mark.asyncio
async def test_every_context_line_prefix_is_on_the_closed_list():
    upd = updater_mod.NarrativeUpdater("agent_test")

    context = await upd._build_update_context(_narrative(), _event())

    prefixes = []
    for line in context.split("\n"):
        if not line.strip():
            continue
        prefix = _line_prefix(line)
        if not prefixes or prefixes[-1] != prefix:
            prefixes.append(prefix)
    assert prefixes == [
        "## Current Narrative Information",
        "- Name",
        "- Description",
        "- Current Summary",
        "- Keywords",
        "## Recent Conversation History",
        "<n>.",
        "## Latest Conversation",
        "User Input",
        "Agent Response",
    ], (
        "a new line reached the updater context: %r — everything here flows "
        "through _apply_llm_update's unconditional overwrite of the fields "
        "continuity reads as the anchor (C3, see the tombstone at the top of "
        "updater.py); justify any addition against the anchor-rename evidence "
        "before landing it" % prefixes
    )
    # Second, content-layer guard: no tool payload leaks in anywhere.
    assert "web.log" not in context
