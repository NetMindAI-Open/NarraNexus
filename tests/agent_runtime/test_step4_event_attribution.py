"""
@file_name: test_step4_event_attribution.py
@author: Bin Liang
@date: 2026-08-05
@description: A turn writes exactly ONE row into `events`, whatever the
              narrative-selection width.

Regression pin for the 0802 "对话时序错乱" report. Narrative selection returns
up to `MAX_NARRATIVES_IN_CONTEXT` (3) narratives — the head is the thread the
turn belongs to, the rest are BM25 neighbours pulled in only to enrich the
read-side context. Step 4.4 used to author a *copy* of the event into every
auxiliary narrative (`duplicate_event_for_narrative`), which produced 1-2
phantom rows per turn, stamped at the moment the run finished:
`state='completed'`, `started_at IS NULL`, `tool_call_count=0`, `event_log='[]'`
and the primary row's `final_output`. Replay surfaces that read the `events`
table then showed the same turn several times, positioned by the run's END
time — an already-answered question re-appearing below newer ones.

These tests drive the real step_4 against real SQLite and assert on the DB
rows, not on the step's own progress narration.
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


class _RecordingTrajectory:
    """Stands in for TrajectoryRecorder — step 4.1 only needs the call to work."""

    def __init__(self) -> None:
        self.rounds: list[str] = []

    async def record_round(self, *, narrative_id: str, **_kwargs) -> None:
        self.rounds.append(narrative_id)


class _RecordingMarkdown:
    """Stands in for NarrativeMarkdownManager (step 4.2)."""

    def __init__(self) -> None:
        self.stats: list[str] = []

    async def update_statistics(self, *, narrative_id: str, stats: dict) -> None:
        self.stats.append(narrative_id)


async def _drain(agen) -> None:
    async for _ in agen:
        pass


@pytest.fixture
async def turn(request):
    """Three selected narratives + one in-flight event, all in the real DB.

    The agent_id is derived from the test name so rows cannot collide with
    another test sharing the session-scoped factory database.
    """
    agent_id = f"agent_{abs(hash(request.node.name)) % 10**12:012d}"
    user_id = "user_dup_probe"

    db = await get_db_client()
    event_service = EventService(agent_id)
    narrative_service = NarrativeService(agent_id)
    narrative_service.set_event_service(event_service)

    narratives = [
        await narrative_service.create_narrative(
            agent_id=agent_id, user_id=user_id, title=f"topic-{i}",
        )
        for i in range(3)
    ]

    event = await event_service.create_event(
        agent_id=agent_id,
        user_id=user_id,
        input_content="帮我问问教学专家现在在做什么",
        trigger_type=TriggerType.CHAT,
    )

    ctx = RunContext(
        agent_id=agent_id,
        user_id=user_id,
        input_content="帮我问问教学专家现在在做什么",
        working_source="chat",
        event=event,
        narrative_list=narratives,
        load_result=SimpleNamespace(
            execution_type=ExecutionPath.AGENT_LOOP,
            active_instances=[],
            relationship_graph="",
            changes_explanation={},
            changes_summary={},
        ),
        execution_result=PathExecutionResult(
            final_output="已问羽书了，等TA回复后立刻转告你。",
            execution_steps=[{"type": "tool_call", "tool_name": "send_message"}],
            response_count=1,
        ),
    )

    yield SimpleNamespace(
        agent_id=agent_id,
        db=db,
        ctx=ctx,
        event=event,
        narratives=narratives,
        event_service=event_service,
        narrative_service=narrative_service,
    )


async def _run_step_4(turn) -> None:
    await _drain(
        step_4_persist_results(
            turn.ctx,
            turn.event_service,
            turn.narrative_service,
            _RecordingMarkdown(),
            _RecordingTrajectory(),
            SimpleNamespace(),
        )
    )


async def test_one_turn_writes_exactly_one_event_row(turn):
    """Three narratives selected, one turn → one row in `events`."""
    await _run_step_4(turn)

    rows = await turn.db.get("events", {"agent_id": turn.agent_id})
    assert [r["event_id"] for r in rows] == [turn.event.id]


async def test_no_logless_completed_twin_is_written(turn):
    """The phantom signature from the 0802 report must not appear.

    `state='completed'` + `started_at IS NULL` + `tool_call_count=0` +
    `event_log='[]'` while carrying the turn's `final_output` — that is the
    duplicate, and it is what made replay show answered questions again.
    """
    await _run_step_4(turn)

    rows = await turn.db.get("events", {"agent_id": turn.agent_id})
    twins = [
        r for r in rows
        if r.get("started_at") in (None, "")
        and (r.get("event_log") or "[]") in ("[]", "")
        and (r.get("final_output") or "")
    ]
    assert twins == []


async def test_every_selected_narrative_references_the_same_event(turn):
    """Auxiliary narratives keep their association — by id, not by a copy.

    The read-side feature (a neighbouring thread can replay this exchange via
    `select_events_for_context`) is preserved: `narratives.event_ids` is a
    list, so the many-to-many lives there. Only `events.narrative_id` — the
    single thread the turn was authored into — stays exclusive to the head.
    """
    await _run_step_4(turn)

    for narrative in turn.narratives:
        reloaded = await turn.narrative_service.load_narrative_from_db(narrative.id)
        assert reloaded is not None
        assert reloaded.event_ids == [turn.event.id], (
            f"narrative {narrative.id} lost the event association"
        )

    row = await turn.db.get_one("events", {"event_id": turn.event.id})
    assert row["narrative_id"] == turn.narratives[0].id


async def test_a_narrative_listed_twice_is_visited_once(turn):
    """§4.0 can put an existing auxiliary at the head — visit it once.

    `switch_narrative` re-points `narrative_list[0]` at the thread the agent
    named, and that thread may ALREADY be in the list as a BM25 neighbour. The
    loop then reached one narrative twice: pre-fix that meant a second
    duplicate row, and it still means a second identical `dynamic_summary`
    entry. Real evidence it happens: `evt_f590aef867f14187` in the local DB
    carries the same `narrative_id` as its own primary row.
    """
    turn.ctx.narrative_list = [
        turn.narratives[1],   # routing target, promoted to head
        turn.narratives[0],
        turn.narratives[1],   # ...and still sitting where BM25 put it
    ]

    await _run_step_4(turn)

    rows = await turn.db.get("events", {"agent_id": turn.agent_id})
    assert [r["event_id"] for r in rows] == [turn.event.id]

    head = await turn.narrative_service.load_narrative_from_db(turn.narratives[1].id)
    assert head is not None
    assert head.event_ids == [turn.event.id]
    assert len(head.dynamic_summary) == 1, "the turn was summarised into one thread twice"
