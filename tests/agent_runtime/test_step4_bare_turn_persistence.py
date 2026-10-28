"""
@file_name: test_step4_bare_turn_persistence.py
@date: 2026-08-16
@description: D-10 — a turn with no narrative still owes four books.

`step_4_persist_results` used to bail out entirely when
``ctx.narrative_list`` was empty, taking six unrelated sections with it. The
"no narrative" part of that is deliberate for ephemeral (voice) turns — they
are meant to leave no trace so the next typed message continuity-checks as if
the voice turn never happened. What was NOT deliberate is that the same early
return also skipped:

  * 4.3  events.final_output / event_log / module_instances, and the memory
         index — so `remember` could never find the exchange
  * 4.6  record_cost — the tokens were burnt and never booked

Both are EVENT-level facts. They have nothing to do with which thread the turn
belongs to, and an ephemeral surface never asked for its costs to disappear.
The narrative-level sections (4.1 trajectory, 4.2 markdown stats, 4.4 narrative
updates) stay skipped, because without a narrative they are meaningless.

This matters beyond voice: plan 4-A' lands a "no durable topic" turn bare
whenever there is no anchor and the surface is ephemeral, so the shape has to
be right before the governance batch ships.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _step4_module():
    """The MODULE, not the function.

    `_agent_runtime_steps/__init__.py` re-exports the entry function under the
    submodule's own name, so a plain `import ... as mod` binds the function and
    every monkeypatch lands on the wrong object.
    """
    import importlib

    return importlib.import_module(
        "narranexus.platform.agent_runtime._agent_runtime_steps"
        ".step_4_persist_results"
    )


async def _drain(gen):
    async for _ in gen:
        pass


@pytest.fixture
def bare_ctx():
    """A finished turn that ended up with no narrative."""
    ctx = SimpleNamespace(
        narrative_list=[],
        execution_result=SimpleNamespace(
            final_output="the reply the user already read",
            execution_steps=[{"type": "tool_call"}],
            response_count=1,
            interrupted=False,
            ctx_data=None,
            agent_loop_response=None,
            input_tokens=1200,
            output_tokens=340,
            total_cost_usd=0.004,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            num_turns=1,
            model="deepseek-ai/DeepSeek-V4-Flash",
        ),
        load_result=None,
        event=SimpleNamespace(id="evt_bare", final_output=None, event_log=[]),
        agent_id="agent_x",
        user_id="user_x",
        session=SimpleNamespace(
            session_id="sess_x", last_response=None, last_query="",
            current_narrative_id="nar_previous",
        ),
        input_content="嗯",
        substeps_4=[],
        event_log_entries=None,
        module_instances=None,
        active_instances=[],
        working_source="chat",
        cancellation=None,
        job_instance_id=None,
        user_chat_instances={},
    )
    ctx.main_narrative = None
    return ctx


@pytest.mark.asyncio
async def test_bare_turn_still_writes_the_event(bare_ctx, monkeypatch):
    """4.3 is event-level: the reply must reach the events row."""
    mod = _step4_module()

    event_service = SimpleNamespace(
        update_event_in_db=AsyncMock(),
        update_event_narrative_id=AsyncMock(),
    )
    monkeypatch.setattr(mod, "record_cost", AsyncMock())

    await _drain(
        mod.step_4_persist_results(
            bare_ctx, event_service, AsyncMock(), AsyncMock(), AsyncMock(),
            AsyncMock(),
        )
    )

    event_service.update_event_in_db.assert_awaited_once()
    kwargs = event_service.update_event_in_db.await_args.kwargs
    assert kwargs["event_id"] == "evt_bare"
    assert kwargs["final_output"] == "the reply the user already read"


@pytest.mark.asyncio
async def test_bare_turn_still_books_its_cost(bare_ctx, monkeypatch):
    """4.6 is event-level: tokens burnt are tokens booked.

    An ephemeral turn leaving no THREAD is a product decision; an ephemeral
    turn leaving no COST ROW is an accounting hole.
    """
    mod = _step4_module()

    recorded = AsyncMock()
    monkeypatch.setattr(mod, "record_cost", recorded)
    event_service = SimpleNamespace(
        update_event_in_db=AsyncMock(), update_event_narrative_id=AsyncMock()
    )

    await _drain(
        mod.step_4_persist_results(
            bare_ctx, event_service, AsyncMock(), AsyncMock(), AsyncMock(),
            AsyncMock(),
        )
    )

    recorded.assert_awaited_once()
    assert recorded.await_args.kwargs["event_id"] == "evt_bare"
    assert recorded.await_args.kwargs["input_tokens"] == 1200


@pytest.mark.asyncio
async def test_bare_turn_does_not_repoint_the_continuity_anchor(
    bare_ctx, monkeypatch
):
    """The bare turn has no thread to point at, and must not erase the one the
    user was actually on — `current_narrative_id` stays where it was."""
    mod = _step4_module()

    monkeypatch.setattr(mod, "record_cost", AsyncMock())
    event_service = SimpleNamespace(
        update_event_in_db=AsyncMock(), update_event_narrative_id=AsyncMock()
    )

    await _drain(
        mod.step_4_persist_results(
            bare_ctx, event_service, AsyncMock(), AsyncMock(), AsyncMock(),
            AsyncMock(),
        )
    )

    assert bare_ctx.session.current_narrative_id == "nar_previous"


@pytest.mark.asyncio
async def test_bare_turn_skips_the_narrative_level_sections(
    bare_ctx, monkeypatch
):
    """4.4 has nothing to update — the guard must be per-section, not a
    blanket early return that also takes 4.3/4.6 with it."""
    mod = _step4_module()

    monkeypatch.setattr(mod, "record_cost", AsyncMock())
    narrative_service = SimpleNamespace(update_with_event=AsyncMock())
    event_service = SimpleNamespace(
        update_event_in_db=AsyncMock(), update_event_narrative_id=AsyncMock()
    )

    await _drain(
        mod.step_4_persist_results(
            bare_ctx, event_service, narrative_service, AsyncMock(),
            AsyncMock(), AsyncMock(),
        )
    )

    narrative_service.update_with_event.assert_not_awaited()
    event_service.update_event_narrative_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_nothing_ran_still_persists_nothing(bare_ctx, monkeypatch):
    """The one case the early return was right about: no execution result
    means there is genuinely nothing to write."""
    mod = _step4_module()

    recorded = AsyncMock()
    monkeypatch.setattr(mod, "record_cost", recorded)
    bare_ctx.execution_result = None
    event_service = SimpleNamespace(
        update_event_in_db=AsyncMock(), update_event_narrative_id=AsyncMock()
    )

    await _drain(
        mod.step_4_persist_results(
            bare_ctx, event_service, AsyncMock(), AsyncMock(), AsyncMock(),
            AsyncMock(),
        )
    )

    event_service.update_event_in_db.assert_not_awaited()
    recorded.assert_not_awaited()
