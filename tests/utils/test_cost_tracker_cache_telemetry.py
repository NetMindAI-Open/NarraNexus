"""
@file_name: test_cost_tracker_cache_telemetry.py
@author: Bin Liang
@date: 2026-07-23
@description: Prompt-cache telemetry (W1). Covers the full transfer chain:
response_processor folds cache fields from both provider vocabularies into
accumulate_usage; ExecutionState accumulates cache tokens but keeps the
latest non-None num_turns (a per-run total, never summed); record_cost
persists the three new cost_records columns and stays backward compatible
for callers that don't pass them.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import ResponseProcessor
from xyz_agent_context.utils.cost_tracker import record_cost


def _mk_mock_db():
    m = MagicMock()
    m.insert = AsyncMock(return_value=1)
    return m


def _done_event(data: dict) -> dict:
    return {"type": "raw_response_event", "data": {"type": "response.done", **data}}


# ---------------------------------------------------------------------------
# response_processor: response.done → accumulate_usage args
# ---------------------------------------------------------------------------

def _extract_usage_args(event: dict) -> dict:
    rp = ResponseProcessor()
    results = list(rp.process(event, ExecutionState()))
    assert len(results) == 1
    update = results[0].state_update
    assert update["method"] == "accumulate_usage"
    return update["args"]


def test_done_event_maps_anthropic_cache_vocabulary():
    args = _extract_usage_args(_done_event({
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 4000,
            "cache_creation_input_tokens": 5600,
        },
        "num_turns": 3,
    }))
    assert args["cache_read_tokens"] == 4000
    assert args["cache_creation_tokens"] == 5600
    assert args["num_turns"] == 3


def test_done_event_maps_codex_cached_input_tokens():
    # OpenAI/codex vocabulary: reads only, no write counter, no num_turns.
    args = _extract_usage_args(_done_event({
        "usage": {
            "input_tokens": 50,
            "output_tokens": 5,
            "cached_input_tokens": 1200,
        },
    }))
    assert args["cache_read_tokens"] == 1200
    assert args["cache_creation_tokens"] == 0
    assert args["num_turns"] is None


def test_done_event_without_cache_fields_defaults_to_zero():
    args = _extract_usage_args(_done_event({
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }))
    assert args["cache_read_tokens"] == 0
    assert args["cache_creation_tokens"] == 0
    assert args["num_turns"] is None


# ---------------------------------------------------------------------------
# ExecutionState: merge semantics
# ---------------------------------------------------------------------------

def test_cache_tokens_accumulate_across_usage_events():
    state = ExecutionState()
    state = state.accumulate_usage(
        100, 10, cache_read_tokens=4000, cache_creation_tokens=5600, num_turns=2
    )
    state = state.accumulate_usage(
        200, 20, cache_read_tokens=3000, cache_creation_tokens=0, num_turns=5
    )
    assert state.input_tokens == 300
    assert state.output_tokens == 30
    assert state.cache_read_tokens == 7000
    assert state.cache_creation_tokens == 5600
    # num_turns is a per-run total: latest report wins, never summed.
    assert state.num_turns == 5


def test_num_turns_none_does_not_overwrite_reported_value():
    state = ExecutionState().accumulate_usage(1, 1, num_turns=4)
    state = state.accumulate_usage(1, 1)  # event without num_turns
    assert state.num_turns == 4


def test_num_turns_defaults_to_none_not_zero():
    assert ExecutionState().num_turns is None
    assert ExecutionState().accumulate_usage(1, 1).num_turns is None


# ---------------------------------------------------------------------------
# record_cost: persistence + backward compatibility
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_cost_persists_cache_columns_and_num_turns():
    db = _mk_mock_db()
    await record_cost(
        db=db,
        agent_id="a_1",
        event_id="evt_1",
        call_type="agent_loop",
        model="claude-code",
        input_tokens=100,
        output_tokens=20,
        sdk_cost_usd=0.5,
        cache_read_tokens=7000,
        cache_creation_tokens=5600,
        num_turns=3,
    )
    row = db.insert.await_args.args[1]
    assert row["cache_read_input_tokens"] == 7000
    assert row["cache_creation_input_tokens"] == 5600
    assert row["num_turns"] == 3


@pytest.mark.asyncio
async def test_record_cost_defaults_keep_legacy_callers_working():
    # llm_function / helper SDK callers don't pass the new kwargs.
    db = _mk_mock_db()
    await record_cost(
        db=db,
        agent_id="a_1",
        event_id=None,
        call_type="llm_function",
        model="gemini-2.5-flash",
        input_tokens=10,
        output_tokens=2,
    )
    row = db.insert.await_args.args[1]
    assert row["cache_read_input_tokens"] == 0
    assert row["cache_creation_input_tokens"] == 0
    assert row["num_turns"] is None
