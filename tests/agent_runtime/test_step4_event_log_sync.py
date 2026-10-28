"""
@file_name: test_step4_event_log_sync.py
@date: 2026-08-12
@description: The in-memory Event step 4.4 hands downstream must carry this
              turn's event_log, not an empty list.

Step 4.3 builds `event_log_entries`, writes them to the `events` row, and then
syncs exactly ONE field back onto the in-memory object:

    ctx.event.final_output = execution_result.final_output

`ctx.event.event_log` stayed at the `[]` it was created with (`_event_impl/
crud.py`), so every later consumer of `ctx.event` was told the turn ran no
steps. That half-sync has already cost us once — the 0802 "对话时序错乱"
report was phantom event rows carrying `event_log='[]'`, copied from this same
in-memory object (see `test_step4_event_attribution.py`).

It bit a second time at defect A1 (2026-08-12): the narrative updater briefly
read `event.event_log` to put the turn's tool actions into the retrieval
surface, and reading it off the stale in-memory object made that fix a silent
no-op in production while every unit test — building its own Event — stayed
green. That consumer is gone: the digest was deleted 2026-08-26 after it was
measured renaming continuity anchors (C3, see the tombstone in updater.py).
The sync's living consumers are the persisted `events` row and the section-5
hooks; these tests pin the sync for them, so a half-synced object cannot lie
to a downstream reader quietly again.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from narranexus.platform.agent_runtime._agent_runtime_steps.context import RunContext
from narranexus.platform.agent_runtime._agent_runtime_steps.step_4_persist_results import (
    step_4_persist_results,
)
from narranexus.platform.narrative import EventService, NarrativeService
from narranexus.platform.narrative.models import TriggerType
from narranexus.platform.schema import ExecutionPath, PathExecutionResult
from narranexus.platform.utils.db.db_factory import get_db_client

# Shaped like a real Claude-side step (survey §3: {tool_name, arguments, …}).
EXECUTION_STEPS = [
    {"type": "thinking", "content": "where would the deploy script live"},
    {"type": "tool_call", "tool_name": "Read",
     "arguments": {"file_path": "/tmp/project/.evermemos/web.log"}},
    {"type": "tool_output", "output": "[Errno 48] Address already in use"},
    {"type": "tool_call", "tool_name": "mcp__chat_module__send_message_to_user_directly",
     "arguments": {"agent_id": "a", "user_id": "u", "content": "端口 1995 被占用"}},
    {"type": "tool_output", "output": '{"success": true}'},
]


class _RecordingMarkdown:
    async def update_statistics(self, *, narrative_id: str, stats: dict) -> None:
        pass


class _RecordingTrajectory:
    async def record_round(self, *, narrative_id: str, **_kwargs) -> None:
        pass


async def _drain(agen) -> None:
    async for _ in agen:
        pass


@pytest.fixture
async def turn(request):
    agent_id = f"agent_{abs(hash(request.node.name)) % 10**12:012d}"
    user_id = "user_log_sync"

    db = await get_db_client()
    event_service = EventService(agent_id)
    narrative_service = NarrativeService(agent_id)
    narrative_service.set_event_service(event_service)

    narrative = await narrative_service.create_narrative(
        agent_id=agent_id, user_id=user_id, title="部署脚本报错",
    )
    event = await event_service.create_event(
        agent_id=agent_id,
        user_id=user_id,
        input_content="帮我查一下部署脚本的报错",
        trigger_type=TriggerType.CHAT,
    )

    ctx = RunContext(
        agent_id=agent_id,
        user_id=user_id,
        input_content="帮我查一下部署脚本的报错",
        working_source="chat",
        event=event,
        narrative_list=[narrative],
        load_result=SimpleNamespace(
            execution_type=ExecutionPath.AGENT_LOOP,
            active_instances=[],
            relationship_graph="",
            changes_explanation={},
            changes_summary={},
        ),
        execution_result=PathExecutionResult(
            # The A1-shaped turn: the answer went out through a tool, so the
            # agent's own final_output is a meta-comment carrying no nouns.
            final_output="Good — I've already sent the findings.",
            execution_steps=EXECUTION_STEPS,
            response_count=1,
        ),
    )

    yield SimpleNamespace(
        agent_id=agent_id, db=db, ctx=ctx, event=event,
        event_service=event_service, narrative_service=narrative_service,
    )


async def _run_step_4(turn) -> None:
    await _drain(
        step_4_persist_results(
            turn.ctx, turn.event_service, turn.narrative_service,
            _RecordingMarkdown(), _RecordingTrajectory(), SimpleNamespace(),
        )
    )


async def test_in_memory_event_log_is_synced_after_step_4(turn):
    """The object handed to 4.4 must describe the turn that actually ran."""
    assert turn.ctx.event.event_log == []  # premise: it starts empty

    await _run_step_4(turn)

    assert len(turn.ctx.event.event_log) == len(EXECUTION_STEPS)
    assert [e.type for e in turn.ctx.event.event_log] == [
        s["type"] for s in EXECUTION_STEPS
    ]


async def test_synced_event_log_carries_the_turn_content_faithfully(turn):
    """Content fidelity of the sync, asserted on the synced log itself.

    This used to be asserted through build_action_digest — deleted at
    a9260baa4^.. (C3: digest content renamed continuity anchors; see the
    tombstone in updater.py). The sync's living consumers are the persisted
    row and the section-5 hooks, and what still matters is that the CONTENT
    survives the round trip — a length/type match alone would pass with
    truncated bodies.
    """
    await _run_step_4(turn)

    log = turn.ctx.event.event_log
    tool_texts = " ".join(
        str(e.content) for e in log if e.type in ("tool_call", "tool_output")
    )
    assert "web.log" in tool_texts
    assert "端口" in tool_texts
    thinking = [e for e in log if e.type == "thinking"]
    assert thinking, "thinking entries are part of the synced log"


async def test_in_memory_event_log_matches_the_persisted_row(turn):
    """In-memory and on-disk must not disagree about what happened."""
    await _run_step_4(turn)

    rows = await turn.db.get("events", {"event_id": turn.event.id})
    persisted = rows[0]["event_log"]

    for step in EXECUTION_STEPS:
        assert step["type"] in persisted
    assert len(turn.ctx.event.event_log) == persisted.count('"timestamp"')
