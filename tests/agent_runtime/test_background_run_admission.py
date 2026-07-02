"""
@file_name: test_background_run_admission.py
@date: 2026-06-18
@description: Verify that BackgroundRun.drive enters the admission controller
slot for the run's user_id before consuming runtime.run.

Two complementary tests:
1. Spy test — monkeypatches get_admission_controller (as seen by
   background_run module) and asserts the slot was entered with the
   correct user_id before the fake runtime generator was iterated.
2. Gating test — injects a controller with max_loops_global=1 and
   starts two concurrent drive() calls; asserts that inside-run
   concurrency never exceeds 1 (the second run waits for the first
   to release).

Both tests work at the public seam (drive()) without touching private
methods, and comply with binding rule #14 (no timeout/iteration cap
in production code).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from unittest.mock import MagicMock, patch

import pytest

from xyz_agent_context.agent_runtime.admission import (
    AgentAdmissionController,
    reset_admission_controller_for_test,
)
from xyz_agent_context.agent_runtime.background_run import BackgroundRun
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.schema_registry import auto_migrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_db() -> AsyncDatabaseClient:
    """Minimal in-memory DB with all tables, enough for BackgroundRun init."""
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    return await AsyncDatabaseClient.create_with_backend(backend)


async def _seed_events_row(
    db: AsyncDatabaseClient,
    event_id: str,
    agent_id: str = "agent_test",
    user_id: str = "u_test",
) -> None:
    """Pre-seed the events row that drive() later flips to 'running'."""
    await db.insert(
        "events",
        {
            "event_id": event_id,
            "trigger": "chat",
            "trigger_source": "test",
            "agent_id": agent_id,
            "user_id": user_id,
            "state": "completed",
            "created_at": "2026-06-18T00:00:00",
            "updated_at": "2026-06-18T00:00:00",
        },
    )


def _make_background_run(
    db: AsyncDatabaseClient,
    active_runs: dict,
    agent_id: str = "agent_test",
    user_id: str = "u_test",
) -> BackgroundRun:
    return BackgroundRun(
        agent_id=agent_id,
        user_id=user_id,
        input_preview="hello",
        db=db,
        active_runs=active_runs,
    )


# ---------------------------------------------------------------------------
# A fake AgentRuntime whose run() is controllable from the test
# ---------------------------------------------------------------------------


class _FakeRuntime:
    """Thin stub that replaces AgentRuntime inside drive().

    run() is an async generator that:
    1. Yields a single step-0 RUNNING progress event (so drive() can
       bind run_id via _on_run_id_assigned).
    2. Waits on a caller-supplied asyncio.Event before yielding the
       final done_event and returning.

    When used as an async context manager it just yields self so that
    ``async with AgentRuntime() as runtime`` inside drive() works.
    """

    def __init__(self, event_id: str, proceed: asyncio.Event) -> None:
        self._event_id = event_id
        self._proceed = proceed

    async def run(self, **_kwargs) -> AsyncGenerator:  # type: ignore[return]
        # Step-0 progress event carries the event_id that drive() extracts
        # via _try_extract_event_id.
        yield {
            "type": "progress",
            "stage": "step_0_running",
            "details": {"event_id": self._event_id},
        }
        await self._proceed.wait()
        yield {"type": "done"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Test 1: spy — slot(user_id) entered before runtime.run iterated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_enters_slot_before_iterating_runtime_run():
    """drive() must call get_admission_controller().slot(user_id) as a
    context manager wrapping the runtime.run() iteration."""
    db = await _make_db()
    await _seed_events_row(db, "evt_spy1", user_id="spy_user")
    active_runs: dict = {}
    bg = _make_background_run(db, active_runs, user_id="spy_user")

    slot_user_ids: list[str] = []
    iteration_order: list[str] = []

    class _SpyController:
        @asynccontextmanager
        async def slot(self, user_id: str):
            slot_user_ids.append(user_id)
            iteration_order.append("slot_entered")
            yield
            iteration_order.append("slot_exited")

    proceed = asyncio.Event()
    proceed.set()  # let the fake runtime finish immediately

    fake_runtime = _FakeRuntime("evt_spy1", proceed)

    class _FakeAgentRuntime:
        """Replaces AgentRuntime inside drive's lazy import."""

        def __init__(self):
            pass

        async def __aenter__(self):
            iteration_order.append("runtime_entered")
            return fake_runtime

        async def __aexit__(self, *_):
            pass

    spy_controller = _SpyController()

    # Patch both the admission singleton (as seen by background_run module)
    # and the AgentRuntime class at its definition site.
    # drive() uses a lazy import (`from ...agent_runtime import AgentRuntime`)
    # which resolves the name from the agent_runtime module at call time, so
    # we must patch it there — not in background_run's namespace.
    with (
        patch(
            "xyz_agent_context.agent_runtime.background_run.get_admission_controller",
            return_value=spy_controller,
        ),
        patch(
            "xyz_agent_context.agent_runtime.agent_runtime.AgentRuntime",
            _FakeAgentRuntime,
        ),
    ):
        await bg.drive(
            agent_id="agent_test",
            user_id="spy_user",
            input_content="test",
            working_source=None,
        )

    assert slot_user_ids == ["spy_user"], (
        f"Expected slot called with 'spy_user', got {slot_user_ids}"
    )
    # slot must be entered BEFORE the AgentRuntime context (which wraps run())
    assert iteration_order.index("slot_entered") < iteration_order.index(
        "runtime_entered"
    ), f"slot_entered must come before runtime_entered; order={iteration_order}"

    await db.close()


# ---------------------------------------------------------------------------
# Test 2: gating — global cap=1 means two concurrent drives never overlap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_respects_global_loop_cap():
    """With max_loops_global=1, two concurrent drive() calls must never
    have both inside runtime.run() simultaneously.  The second run must
    queue at the admission slot until the first completes."""
    reset_admission_controller_for_test(
        AgentAdmissionController(
            max_users=None,
            max_loops_per_user=None,
            max_loops_global=1,
            min_free_mem_mb=0,
        )
    )
    try:
        db1 = await _make_db()
        db2 = await _make_db()
        await _seed_events_row(db1, "evt_gate1", user_id="gate_u1")
        await _seed_events_row(db2, "evt_gate2", user_id="gate_u2")
        active_runs: dict = {}

        bg1 = _make_background_run(db1, active_runs, user_id="gate_u1")
        bg2 = _make_background_run(db2, active_runs, user_id="gate_u2")

        concurrency_counter = 0
        max_observed_concurrency = 0
        first_run_proceed = asyncio.Event()

        class _TrackingRuntime:
            def __init__(self, event_id: str, proceed: asyncio.Event):
                self._event_id = event_id
                self._proceed = proceed

            async def run(self, **_kwargs):
                nonlocal concurrency_counter, max_observed_concurrency
                concurrency_counter += 1
                if concurrency_counter > max_observed_concurrency:
                    max_observed_concurrency = concurrency_counter
                yield {
                    "type": "progress",
                    "stage": "step_0_running",
                    "details": {"event_id": self._event_id},
                }
                await self._proceed.wait()
                yield {"type": "done"}
                concurrency_counter -= 1

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

        second_run_proceed = asyncio.Event()
        runtime1 = _TrackingRuntime("evt_gate1", first_run_proceed)
        runtime2 = _TrackingRuntime("evt_gate2", second_run_proceed)
        runtime_call_count = 0

        class _FakeAgentRuntimeFactory:
            def __init__(self):
                pass

            async def __aenter__(self):
                nonlocal runtime_call_count
                runtime_call_count += 1
                if runtime_call_count == 1:
                    return runtime1
                return runtime2

            async def __aexit__(self, *_):
                pass

        with patch(
            "xyz_agent_context.agent_runtime.agent_runtime.AgentRuntime",
            _FakeAgentRuntimeFactory,
        ):
            # Start both drives concurrently.
            t1 = asyncio.create_task(
                bg1.drive(
                    agent_id="agent_test",
                    user_id="gate_u1",
                    input_content="msg1",
                    working_source=None,
                )
            )
            t2 = asyncio.create_task(
                bg2.drive(
                    agent_id="agent_test",
                    user_id="gate_u2",
                    input_content="msg2",
                    working_source=None,
                )
            )

            # Give both tasks a chance to proceed past the admission gate
            # (or block at it).
            await asyncio.sleep(0.1)

            # Release first run — the second should now be admitted.
            first_run_proceed.set()
            await asyncio.sleep(0.1)
            second_run_proceed.set()

            await asyncio.gather(t1, t2)

        assert max_observed_concurrency <= 1, (
            f"Expected at most 1 concurrent run inside runtime.run(); "
            f"observed {max_observed_concurrency}"
        )

        await db1.close()
        await db2.close()
    finally:
        reset_admission_controller_for_test(None)
