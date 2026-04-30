"""
@file_name: test_end_to_end_trace.py
@author: Bin Liang
@date: 2026-04-28
@description: End-to-end test that mirrors the trigger → AgentRuntime → step
log shape and proves a single event_id can be greppped out of the file
across multiple subsystems. T20 acceptance: any one user message must be
recoverable as one trace from disk.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from loguru import logger

from xyz_agent_context.utils.logging import bind_event, setup_logging, timed
from xyz_agent_context.utils.logging._setup import _reset_for_tests


@pytest.fixture(autouse=True)
def _reset() -> None:
    logger.remove()
    logger.configure(extra={})
    _reset_for_tests()
    yield
    logger.remove()
    logger.configure(extra={})
    _reset_for_tests()


def test_event_trace_recoverable_from_log_file(tmp_path: Path) -> None:
    """One bind_event scope generates lines that all carry the same
    event_id token, recoverable by a single grep."""
    setup_logging("smoke_service", log_dir=tmp_path)

    @timed("step.fake_step_a")
    def step_a() -> None:
        logger.info("inside step_a")

    @timed("step.fake_step_b")
    async def step_b() -> None:
        logger.info("inside step_b")
        with timed("hook.module_x.data_gathering"):
            logger.debug("hook details (only on DEBUG)")
        logger.info("after hook")

    async def fake_run() -> None:
        run_id = "run_abc12345"
        trigger_id = "lark_om_msg_xyz"
        event_id = "evt_a1b2c3d4e5f6"
        with bind_event(
            run_id=run_id,
            trigger_id=trigger_id,
            agent_id="agent_smoke",
            user_id="user_smoke",
        ):
            logger.info("trigger received from {}", trigger_id)
            step_a()
            with bind_event(event_id=event_id):
                logger.info("step 0 created event {}", event_id)
                await step_b()
                logger.info("agent run completed")
            logger.info("trigger about to ack")

    asyncio.run(fake_run())
    logger.complete()

    files = list((tmp_path / "smoke_service").glob("smoke_service_*.log"))
    assert files, "log file not produced"
    body = files[0].read_text(encoding="utf-8")

    # 1. Every step / hook / inline message produced a TIMED line.
    assert "[TIMED] step.fake_step_a ok" in body
    assert "[TIMED] step.fake_step_b ok" in body
    assert "[TIMED] hook.module_x.data_gathering ok" in body

    # 2. Lines emitted before bind_event(event_id=...) carry the run_id
    #    but do not carry the event_id (i.e. before step 0).
    pre_event_lines = [
        ln
        for ln in body.splitlines()
        if "trigger received" in ln or "step.fake_step_a ok" in ln
    ]
    assert pre_event_lines, "expected pre-event lines"
    for line in pre_event_lines:
        assert "run_abc12345" in line
        assert "evt_a1b2c3d4e5f6" not in line

    # 3. Lines emitted inside the event scope carry both run_id and
    #    event_id.
    event_lines = [
        ln
        for ln in body.splitlines()
        if "evt_a1b2c3d4e5f6" in ln
    ]
    assert len(event_lines) >= 4, (
        f"expected ≥4 lines tagged with event_id; got {len(event_lines)}"
    )
    for line in event_lines:
        assert "run_abc12345" in line, f"event line missing run_id: {line}"

    # 4. After exiting the event scope (still inside the outer bind),
    #    event_id is gone but run_id remains.
    post_event = [ln for ln in body.splitlines() if "trigger about to ack" in ln]
    assert post_event, "post-event line not found"
    for line in post_event:
        assert "run_abc12345" in line
        assert "evt_a1b2c3d4e5f6" not in line

    # 5. Single grep over event_id pulls a coherent slice (≥4 lines
    #    spanning the bound section).
    grep_hits = [ln for ln in body.splitlines() if "evt_a1b2c3d4e5f6" in ln]
    titles = [
        "step 0 created event",
        "[TIMED] step.fake_step_b ok",
        "[TIMED] hook.module_x.data_gathering ok",
        "agent run completed",
    ]
    for marker in titles:
        assert any(marker in ln for ln in grep_hits), (
            f"event grep missing line containing {marker!r}"
        )
