"""
@file_name: test_cache_telemetry_chain_sqlite.py
@author: Bin Liang
@date: 2026-07-23
@description: End-to-end chain test for the W1 prompt-cache telemetry —
everything real except the CLI subprocess. A synthetic ResultMessage goes
through output_transfer → ResponseProcessor → ExecutionState, then
record_cost INSERTs into a real SQLite database created by auto_migrate.
This is the test that catches a column-name mismatch between the INSERT
dict and schema_registry (mocked-db unit tests cannot).
"""
from types import SimpleNamespace

import pytest

from xyz_agent_context.agent_framework.loop.output_transfer import (
    _convert_result_to_stream_event,
)
from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import ResponseProcessor
from xyz_agent_context.utils.cost_tracker import record_cost


@pytest.mark.asyncio
async def test_result_message_lands_in_cost_records(tmp_path):
    # --- Real SQLite DB with the registry schema -------------------------
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(str(tmp_path / "chain.db"))
    await backend.initialize()
    await auto_migrate(backend)

    class _Db:
        """Minimal AsyncDatabaseClient stand-in over the real backend."""

        def __init__(self, b):
            self._b = b

        async def insert(self, table, data):
            return await self._b.insert(table, data)

    db = _Db(backend)

    # --- CLI boundary: synthetic ResultMessage ---------------------------
    result_message = SimpleNamespace(
        usage={
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_input_tokens": 4623,
            "cache_creation_input_tokens": 5640,
        },
        total_cost_usd=0.042,
        num_turns=3,
        session_id="sess_test_1234",
        stop_reason="end_turn",
    )
    event = _convert_result_to_stream_event(result_message)

    # --- response_processor → ExecutionState -----------------------------
    rp = ResponseProcessor()
    state = ExecutionState()
    for processed in rp.process(event, state):
        state = rp.apply_state_update(state, processed)

    assert state.cache_read_tokens == 4623
    assert state.cache_creation_tokens == 5640
    assert state.num_turns == 3

    # --- step_4 equivalent: record_cost against the real schema ----------
    await record_cost(
        db=db,
        agent_id="agent_chain_test",
        event_id="evt_chain_test",
        call_type="agent_loop",
        model=state.model or "claude-code",
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        sdk_cost_usd=state.total_cost_usd or None,
        cache_read_tokens=state.cache_read_tokens,
        cache_creation_tokens=state.cache_creation_tokens,
        num_turns=state.num_turns,
    )

    rows = await backend.get(
        "cost_records", {"agent_id": "agent_chain_test"}
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["input_tokens"] == 120
    assert row["cache_read_input_tokens"] == 4623
    assert row["cache_creation_input_tokens"] == 5640
    assert row["num_turns"] == 3

    await backend.close()
