"""
@file_name: test_client_recording.py
@author:
@date: 2026-07-31
@description: Trigger runs become observable through the client seam.

InProcessAgentRuntimeClient mounts a RunRecorder around every run, so a
Lark/team/job run leaves the same live trace (event_stream + events
state machine) as a WS chat run — that is the whole premise of the
universal run-observation design. These tests drive the real client
with a fake runtime and assert the persisted trace.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime.client import InProcessAgentRuntimeClient
from xyz_agent_context.agent_runtime.run_recorder import (
    RECORDING_DISABLED_ENV,
    STATE_COMPLETED,
    STATE_FAILED,
)
from xyz_agent_context.schema.runtime_message import MessageType


class _WireMsg:
    """Fake runtime message carrying BOTH the typed attrs collect_run
    reads AND the wire dict the recorder normalises via to_dict()."""

    def __init__(self, wire: dict, message_type=None, delta=None):
        self._wire = wire
        self.message_type = message_type
        self.delta = delta
        self.raw = None
        # collect_run reads typed attrs (details.event_id, tool names)
        # off the message object, exactly like real ProgressMessages.
        self.details = wire.get("details")
        self.tool_name = (wire.get("details") or {}).get("tool_name")

    def to_dict(self) -> dict:
        return self._wire


def _canned_events(event_id: str) -> list[_WireMsg]:
    return [
        _WireMsg(
            {"type": "progress", "step": "0", "status": "completed",
             "details": {"event_id": event_id}},
        ),
        _WireMsg(
            {"type": "agent_thinking", "thinking_content": "pondering"},
        ),
        _WireMsg(
            {"type": "progress", "step": "3.4.1", "title": "🔧 Bash",
             "details": {"tool_name": "Bash", "arguments": {"command": "ls"}}},
        ),
        _WireMsg(
            {"type": "agent_response", "delta": "done"},
            message_type=MessageType.AGENT_RESPONSE, delta="done",
        ),
    ]


class _FakeRuntime:
    def __init__(self, events, fail_after=None):
        self._events = events
        self._fail_after = fail_after

    def run(self, **kwargs):
        async def _gen():
            for i, e in enumerate(self._events):
                if self._fail_after is not None and i >= self._fail_after:
                    raise RuntimeError("runtime crashed")
                yield e
        return _gen()


async def _seed_events_row(db, event_id: str):
    await db.insert("events", {
        "event_id": event_id,
        "trigger": "lark",
        "trigger_source": "test",
        "agent_id": "agent_test",
        "user_id": "u_test",
        "state": "completed",
        "created_at": "2026-07-31T00:00:00",
        "updated_at": "2026-07-31T00:00:00",
    })


@pytest.fixture
def patch_stack(monkeypatch, db_client):
    """Point the client's lazy imports at the fakes: runtime factory is
    swapped per-test; get_db_client returns the test DB."""

    async def fake_get_db_client():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client",
        fake_get_db_client,
    )

    def set_runtime(rt):
        monkeypatch.setattr(
            "xyz_agent_context.agent_runtime.agent_runtime.AgentRuntime",
            lambda: rt,
        )

    return set_runtime


@pytest.mark.asyncio
async def test_run_and_collect_records_full_trace(patch_stack, db_client):
    await _seed_events_row(db_client, "evt_trig1")
    patch_stack(_FakeRuntime(_canned_events("evt_trig1")))

    result = await InProcessAgentRuntimeClient().run_and_collect(
        agent_id="agent_test", user_id="u_test",
        input_content="hi", working_source="lark",
    )

    # collect_run behaviour untouched by the tap
    assert result.output_text == "done"
    assert result.event_id == "evt_trig1"

    row = await db_client.get_one("events", {"event_id": "evt_trig1"})
    assert row["state"] == STATE_COMPLETED
    assert row["tool_call_count"] == 1
    assert row["finished_at"] is not None

    kinds = sorted(
        r["kind"] for r in
        await db_client.get("event_stream", {"event_id": "evt_trig1"})
    )
    assert kinds == ["progress", "text_delta", "thinking_segment", "tool_call"]


@pytest.mark.asyncio
async def test_runtime_crash_finalizes_failed(patch_stack, db_client):
    await _seed_events_row(db_client, "evt_trig2")
    patch_stack(_FakeRuntime(_canned_events("evt_trig2"), fail_after=2))

    with pytest.raises(RuntimeError, match="runtime crashed"):
        await InProcessAgentRuntimeClient().run_and_collect(
            agent_id="agent_test", user_id="u_test",
            input_content="hi", working_source="lark",
        )

    row = await db_client.get_one("events", {"event_id": "evt_trig2"})
    assert row["state"] == STATE_FAILED
    assert "runtime crashed" in (row["error_message"] or "")


@pytest.mark.asyncio
async def test_kill_switch_disables_recording(patch_stack, db_client, monkeypatch):
    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    await _seed_events_row(db_client, "evt_trig3")
    patch_stack(_FakeRuntime(_canned_events("evt_trig3")))

    result = await InProcessAgentRuntimeClient().run_and_collect(
        agent_id="agent_test", user_id="u_test",
        input_content="hi", working_source="lark",
    )
    assert result.output_text == "done"  # the run itself is unaffected

    row = await db_client.get_one("events", {"event_id": "evt_trig3"})
    assert row["state"] == "completed"  # untouched seed value, no lifecycle writes
    assert row.get("started_at") is None
    assert await db_client.get("event_stream", {"event_id": "evt_trig3"}) == []


@pytest.mark.asyncio
async def test_run_stream_records_and_yields(patch_stack, db_client):
    await _seed_events_row(db_client, "evt_trig4")
    patch_stack(_FakeRuntime(_canned_events("evt_trig4")))

    seen = []
    async for event in InProcessAgentRuntimeClient().run_stream(
        agent_id="agent_test", user_id="u_test", input_content="hi",
    ):
        seen.append(event)
    assert len(seen) == 4  # consumer sees every original message

    row = await db_client.get_one("events", {"event_id": "evt_trig4"})
    assert row["state"] == STATE_COMPLETED
    kinds = sorted(
        r["kind"] for r in
        await db_client.get("event_stream", {"event_id": "evt_trig4"})
    )
    assert kinds == ["progress", "text_delta", "thinking_segment", "tool_call"]
