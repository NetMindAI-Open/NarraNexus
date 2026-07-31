"""
@file_name: test_cli_session_id_threading.py
@author:
@date: 2026-07-24
@description: R1 capture chain for the resumable CLI session handle —
a synthetic ResultMessage carrying session_id goes through
output_transfer → ResponseProcessor → ExecutionState → PathExecutionResult
(mirrors tests/utils/test_cost_tracker_cache_telemetry.py's style for the
W1 num_turns chain the field rides along).
"""
from types import SimpleNamespace

from xyz_agent_context.agent_framework.loop.output_transfer import (
    _convert_result_to_stream_event,
)
from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import ResponseProcessor
from xyz_agent_context.schema.decision_schema import PathExecutionResult


def _result_message(**overrides) -> SimpleNamespace:
    base = dict(
        usage={"input_tokens": 100, "output_tokens": 20},
        total_cost_usd=0.01,
        num_turns=2,
        session_id="cli_sess_abc123",
        stop_reason="end_turn",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run_through_processor(event: dict, state: ExecutionState | None = None) -> ExecutionState:
    rp = ResponseProcessor()
    state = state or ExecutionState()
    for processed in rp.process(event, state):
        state = rp.apply_state_update(state, processed)
    return state


# ---------------------------------------------------------------------------
# output_transfer: ResultMessage.session_id → response.done data
# ---------------------------------------------------------------------------

def test_result_message_session_id_enters_event_data():
    event = _convert_result_to_stream_event(_result_message())
    assert event["data"]["session_id"] == "cli_sess_abc123"


def test_result_message_without_session_id_omits_key():
    msg = _result_message()
    del msg.session_id
    event = _convert_result_to_stream_event(msg)
    assert "session_id" not in event["data"]


# ---------------------------------------------------------------------------
# response_processor → ExecutionState
# ---------------------------------------------------------------------------

def test_session_id_reaches_execution_state():
    event = _convert_result_to_stream_event(_result_message())
    state = _run_through_processor(event)
    assert state.cli_session_id == "cli_sess_abc123"
    assert state.num_turns == 2  # the ride-along field is unharmed


def test_absent_session_id_leaves_state_none():
    msg = _result_message()
    del msg.session_id
    state = _run_through_processor(_convert_result_to_stream_event(msg))
    assert state.cli_session_id is None


def test_latest_non_none_session_id_wins_never_accumulates():
    state = ExecutionState().accumulate_usage(1, 1, cli_session_id="cli_first")
    state = state.accumulate_usage(1, 1, cli_session_id="cli_second")
    assert state.cli_session_id == "cli_second"
    # An event without a handle must NOT erase a reported one.
    state = state.accumulate_usage(1, 1)
    assert state.cli_session_id == "cli_second"


# ---------------------------------------------------------------------------
# PathExecutionResult: step_3 assembly shape
# ---------------------------------------------------------------------------

def test_path_execution_result_carries_cli_fields():
    state = _run_through_processor(_convert_result_to_stream_event(_result_message()))
    result = PathExecutionResult(
        cli_session_id=state.cli_session_id,
    )
    assert result.cli_session_id == "cli_sess_abc123"


def test_path_execution_result_cli_fields_default_none():
    # DIRECT_TRIGGER / non-Claude paths never fill these.
    result = PathExecutionResult()
    assert result.cli_session_id is None
