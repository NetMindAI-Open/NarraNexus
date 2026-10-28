"""
@file_name: test_cost_tracker_event_attribution.py
@author: Bin Liang
@date: 2026-08-28
@description: Ambient event attribution for cost rows.

Only step_4 (call_type="agent_loop") ever passed a real event_id; every
helper call site hard-coded event_id=None, so a per-turn token figure
counted the main loop and silently dropped narrative selection, the
shutter/decider, summarisation and post-turn hooks. The fix is an ambient
ContextVar that record_cost falls back to, mirroring how (agent_id, db)
already travel — these tests pin the fallback, the explicit-wins rule, the
scope restore, and the task-copy propagation that background Steps 5-6
depend on.
"""
import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from narranexus.platform.utils.cost_tracker import (
    clear_cost_context,
    cost_context_scope,
    cost_event_scope,
    get_cost_context,
    get_cost_event_id,
    record_cost,
    set_cost_context,
)


def _mk_mock_db():
    m = MagicMock()
    m.insert = AsyncMock(return_value=1)
    return m


def _inserted_row(db) -> dict:
    assert db.insert.await_count == 1
    table, row = db.insert.await_args.args
    assert table == "cost_records"
    return row


@pytest.fixture(autouse=True)
def _isolate_context():
    """Each test starts and ends with a clean ambient context."""
    clear_cost_context()
    yield
    clear_cost_context()


# ---------------------------------------------------------------------------
# record_cost fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_helper_call_inherits_ambient_event_id():
    """A helper passing event_id=None lands on the turn that is running."""
    db = _mk_mock_db()
    with cost_event_scope("evt_a1b2c3d4"):
        await record_cost(
            db=db, agent_id="agent_x", event_id=None,
            call_type="llm_function", model="gpt-4o",
            input_tokens=10, output_tokens=5,
        )
    assert _inserted_row(db)["event_id"] == "evt_a1b2c3d4"


@pytest.mark.asyncio
async def test_explicit_event_id_wins_over_ambient():
    """step_4 passes ctx.event.id explicitly; it must not be second-guessed."""
    db = _mk_mock_db()
    with cost_event_scope("evt_ambient"):
        await record_cost(
            db=db, agent_id="agent_x", event_id="evt_explicit",
            call_type="agent_loop", model="claude-opus-5",
            input_tokens=10, output_tokens=5,
        )
    assert _inserted_row(db)["event_id"] == "evt_explicit"


@pytest.mark.asyncio
async def test_no_ambient_event_stays_null():
    """Outside a turn (background worker, script) the column stays NULL."""
    db = _mk_mock_db()
    await record_cost(
        db=db, agent_id="agent_x", event_id=None,
        call_type="llm_function", model="gpt-4o",
        input_tokens=10, output_tokens=5,
    )
    assert _inserted_row(db)["event_id"] is None


# ---------------------------------------------------------------------------
# scope lifecycle
# ---------------------------------------------------------------------------

def test_scope_restores_previous_value_on_exit():
    assert get_cost_event_id() is None
    with cost_event_scope("evt_outer"):
        assert get_cost_event_id() == "evt_outer"
        with cost_event_scope("evt_inner"):
            assert get_cost_event_id() == "evt_inner"
        assert get_cost_event_id() == "evt_outer"
    assert get_cost_event_id() is None


def test_scope_restores_even_when_body_raises():
    with pytest.raises(RuntimeError):
        with cost_event_scope("evt_boom"):
            raise RuntimeError("turn failed")
    assert get_cost_event_id() is None


def test_clear_cost_context_also_clears_the_event():
    """The outermost clear must not leave a turn id behind for the next caller
    in this context — a stale id would misattribute later spend."""
    with cost_event_scope("evt_stale"):
        clear_cost_context()
        assert get_cost_event_id() is None


# ---------------------------------------------------------------------------
# cost_context_scope — the (agent_id, db) half
# ---------------------------------------------------------------------------

def test_context_scope_restores_the_outer_pair_instead_of_clearing_it():
    """The bug this scope exists for.

    step_3's fallback helper used to `set_cost_context(...)` then
    `clear_cost_context()` in a finally. That runs inside an ASYNC GENERATOR,
    which does not get its own context copy — so the clear wiped the turn's
    own (agent_id, db). Everything after it in the turn recorded nothing, and
    Steps 5-6 (spawned later, copying that emptied context) booked no cost at
    all. Restoring instead of clearing is the fix.
    """
    db_outer, db_inner = object(), object()
    with cost_context_scope("agent_outer", db_outer):
        with cost_context_scope("agent_inner", db_inner):
            assert get_cost_context() == ("agent_inner", db_inner)
        assert get_cost_context() == ("agent_outer", db_outer)
    assert get_cost_context() is None


def test_context_scope_restores_when_the_body_raises():
    db = object()
    with cost_context_scope("agent_outer", db):
        with pytest.raises(RuntimeError):
            with cost_context_scope("agent_inner", object()):
                raise RuntimeError("helper blew up")
        assert get_cost_context() == ("agent_outer", db)


def test_context_scope_leaves_the_event_alone():
    """A mid-turn context re-set must not knock the turn's helper spend off
    the turn — the two vars have different lifetimes on purpose."""
    with cost_event_scope("evt_turn"):
        with cost_context_scope("agent_x", object()):
            assert get_cost_event_id() == "evt_turn"
        assert get_cost_event_id() == "evt_turn"


# ---------------------------------------------------------------------------
# the shape that actually broke: an async generator
# ---------------------------------------------------------------------------
#
# The three tests above prove the scope nests correctly under a plain `with`,
# which it always did. The bug lived in the ONE shape none of them exercise:
# a `with` written inside `async def … yield`. An async generator gets no
# context copy of its own, so its enter/exit land on the CALLER's context —
# which is why the old `set` + `finally: clear` emptied the turn instead of a
# private copy. Reverting step_3 to that shape left all 923 runtime+utils
# tests green, so this is the missing guard rail, not a redundant one.


@pytest.mark.asyncio
async def test_fallback_reply_stream_leaves_the_turns_context_intact():
    """Drives the REAL `step_3._generate_fallback_reply_stream`.

    Deliberately not a hand-rolled async generator of the same shape: that
    would only prove `cost_context_scope` behaves, which is not where the bug
    was. The bug was this specific call site using set + clear, so the guard
    rail has to run this specific function — revert it and this goes red.
    """
    from narranexus.platform.agent_framework.llm import helper_sdk as helper_sdk_mod
    # importlib, not `import … as`: the package's __init__ rebinds the name
    # `step_3_agent_loop` to a same-named FUNCTION, so both import forms hand
    # back that function rather than the module the private helper lives in.
    step_3_agent_loop = importlib.import_module(
        "narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop"
    )

    class _FakeSdk:
        async def llm_stream(self, *, instructions, user_input):
            yield "recovered reply"

    db_turn = object()
    # The turn's own context. Set explicitly: the autouse fixture is sync and
    # pytest-asyncio runs the test body in a Task with a copied context, so
    # the fixture's clear did not land in this one.
    set_cost_context("agent_turn", db_turn)

    with patch.object(helper_sdk_mod, "get_helper_sdk", lambda: _FakeSdk()):
        stream = step_3_agent_loop._generate_fallback_reply_stream(
            mode="no_reply",
            context_messages=[],
            agent_loop_response=[],
            final_output="",
            user_input="hi",
            error_info=None,
            db=object(),
            agent_id="agent_helper",
        )
        async for _ in stream:
            pass

    # Asserted OUTSIDE the generator on purpose — inside, the scope is still
    # active and would report the helper's own pair, missing the point.
    # The pre-fix shape leaves None here, and Steps 5-6 spawn off that.
    assert get_cost_context() == ("agent_turn", db_turn)


@pytest.mark.asyncio
async def test_abandoned_async_generator_still_restores_the_caller():
    """A stream the caller walks away from (user interrupt) still hands the
    turn's pair back.

    `aclose()` here runs in the SAME task that entered the scope — an async
    generator has no context of its own, which is this fix's whole premise —
    so `reset` succeeds normally and `_unwind`'s degradation branch is not
    involved. (That branch is covered by
    `test_scope_exit_survives_a_token_from_another_context` below, which
    really does close from another context; `_unwind` is var-agnostic, so
    `_cost_context` goes through the identical code.)
    """
    db_turn, db_helper = object(), object()

    async def _helper_stream():
        with cost_context_scope("agent_helper", db_helper):
            yield "first"
            yield "second"

    set_cost_context("agent_turn", db_turn)

    gen = _helper_stream()
    async for _ in gen:
        break
    await gen.aclose()  # must not raise

    # Named db_helper, not an inline object(): comparing against a pair the
    # test has no handle on is how the first version of this assertion ended
    # up true no matter what the code did.
    assert get_cost_context() == ("agent_turn", db_turn)


def test_scope_exit_survives_a_token_from_another_context():
    """An async generator abandoned mid-iteration runs its finally under
    whatever context closes it, and `ContextVar.reset` raises
    `Token was created in a different Context` there.

    What is guaranteed: the unwind does not raise into that unrelated
    caller's stack, and the var reads None in the context doing the cleanup.
    What is NOT guaranteed (and cannot be — contextvars has no cross-context
    write): the entering context's value. It keeps the id until its own
    scope unwinds normally. That is why the scope, not the abandonment path,
    is what AgentRuntime relies on.
    """
    import contextvars

    cm = cost_event_scope("evt_cross")
    cm.__enter__()
    seen = {}

    def _exit_elsewhere():
        # Fresh context: the token was created in the caller's, not this one.
        cm.__exit__(None, None, None)  # must not raise ValueError
        seen["after"] = get_cost_event_id()

    contextvars.copy_context().run(_exit_elsewhere)
    assert seen["after"] is None


# ---------------------------------------------------------------------------
# propagation into spawned tasks (background Steps 5-6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawned_task_inherits_the_turns_event_id():
    """Steps 5-6 run in a task spawned from inside the scope: asyncio copies
    the context at create_task time, so post-turn hook spend stays on the turn."""
    seen = {}

    async def _background():
        seen["event_id"] = get_cost_event_id()

    with cost_event_scope("evt_bg"):
        task = asyncio.create_task(_background())
    await task

    assert seen["event_id"] == "evt_bg"
