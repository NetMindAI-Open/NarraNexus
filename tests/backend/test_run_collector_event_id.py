"""
@file_name: test_run_collector_event_id.py
@author:
@date: 2026-07-30
@description: collect_run captures the Step-0 event_id and surfaces it via
    RunCollection.event_id and the optional on_event_id callback.
"""
import pytest

from xyz_agent_context.agent_runtime.run_collector import collect_run
from xyz_agent_context.schema.runtime_message import ProgressMessage, ProgressStatus


class _FakeRuntime:
    """Yields canned messages the way AgentRuntime.run does."""

    def __init__(self, messages):
        self._messages = messages

    async def run(self, **_kwargs):
        for m in self._messages:
            yield m


def _step0(event_id: str) -> ProgressMessage:
    return ProgressMessage(
        step="0", title="Initialization", description="done",
        status=ProgressStatus.COMPLETED,
        details={"agent_name": "a", "event_id": event_id, "session_id": "s"},
    )


@pytest.mark.asyncio
async def test_event_id_lands_on_the_collection():
    col = await collect_run(
        _FakeRuntime([_step0("evt_123")]),
        agent_id="a", user_id="u", input_content="hi", working_source=None,
    )
    assert col.event_id == "evt_123"


@pytest.mark.asyncio
async def test_on_event_id_fires_once_and_swallows_errors():
    seen: list[str] = []

    async def cb(eid: str) -> None:
        seen.append(eid)
        raise RuntimeError("must not break the run")

    col = await collect_run(
        _FakeRuntime([_step0("evt_1"), _step0("evt_2")]),
        agent_id="a", user_id="u", input_content="hi", working_source=None,
        on_event_id=cb,
    )
    assert seen == ["evt_1"]          # first id wins, fired exactly once
    assert col.event_id == "evt_1"


@pytest.mark.asyncio
async def test_no_event_id_is_fine():
    col = await collect_run(
        _FakeRuntime([ProgressMessage(step="1", title="x", description="",
                                      status=ProgressStatus.RUNNING)]),
        agent_id="a", user_id="u", input_content="hi", working_source=None,
    )
    assert col.event_id is None
