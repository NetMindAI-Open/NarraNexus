"""
@file_name: conftest.py
@author: NarraNexus
@date: 2026-08-16
@description: Capture what `run()` hands RunContext, without running a turn.

`AgentRuntime.run()` does real work before it builds the RunContext — resolve
the DB client, load the agent row, replace `user_id` with the owner, set the
cost context, construct services, resolve the turn profile. Every one of those
decisions reaches the rest of the turn through the RunContext kwargs and
nowhere else, which makes those kwargs the one place to assert them.

The seam: spy the module attribute `run()` resolves `RunContext` through, and
raise the moment the kwargs are in hand. No steps execute, no LLM is called,
and the assertion is about the real prologue rather than a re-implementation
of it.

Extracted here when the second copy appeared and the two had already diverged
(one stubbed the DB client, one did not). That prologue has taken two new
parameters in a month; a third copy would mean a third place to fix when it
takes another, and a test going red for reasons unrelated to what it tests.
"""
from __future__ import annotations

import pytest

import narranexus.platform.agent_runtime.agent_runtime as agent_runtime_module
from narranexus.platform.agent_runtime.agent_runtime import AgentRuntime


class CtxCaptured(BaseException):
    """Sentinel that ends the run once the kwargs are captured.

    BaseException is defensive, not currently load-bearing: the RunContext
    construction sits outside every `except Exception` in `run()`, so the
    sentinel propagates either way — downgrading this to Exception today does
    NOT turn any test red, which is exactly why the base class is worth
    stating rather than leaving to memory. It matters the moment the capture
    point moves inside one of those guarded blocks, where a swallowable
    sentinel would make every test built on this fixture pass without ever
    reaching its assertion.
    """


@pytest.fixture
def capture_run_context(monkeypatch):
    """Return ``await capture(**run_kwargs) -> (kwargs, runtime)``.

    Pass ``db_client=`` to serve the agent lookup from a per-test database;
    omit it to leave the runtime's own resolution alone — the two callers want
    different things, and making the stub unconditional would quietly drop the
    "reaches RunContext with no database of its own" coverage.
    """
    async def _capture(*, db_client=None, agent_id="agent_a", user_id="user_u",
                       input_content="hi", **run_kwargs):
        captured: dict = {}

        class _SpyCtx:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                raise CtxCaptured()

        monkeypatch.setattr(agent_runtime_module, "RunContext", _SpyCtx)

        runtime = AgentRuntime()
        if db_client is not None:
            async def _db(*_a, **_k):
                return db_client

            monkeypatch.setattr(runtime, "_ensure_database_client", _db)

        gen = runtime.run(
            agent_id=agent_id, user_id=user_id,
            input_content=input_content, **run_kwargs,
        )
        with pytest.raises(CtxCaptured):
            async for _ in gen:
                pass
        assert captured, "RunContext was never constructed"
        return captured, runtime

    return _capture


@pytest.fixture(autouse=True)
def _reset_im_dm_fallback_history():
    """The DM fallback rate limiter keeps module-level state.

    Scoped to the whole directory, not just the file that tests it:
    ``test_im_dm_fallback_delivery_e2e.py`` drives a real delivery and so
    appends to that map without knowing it. Leaving the reset in one file
    means the next test that asserts on the map's size inherits an initial
    value that depends on execution order — the most expensive kind of
    flake to debug.
    """
    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
        reset_im_dm_fallback_history,
    )

    reset_im_dm_fallback_history()
    yield
    reset_im_dm_fallback_history()


@pytest.fixture(autouse=True)
def _executor_boot_on_private_registries(monkeypatch):
    """The executor lifespan (``executor_service.app``) boots the plugin platform
    and FREEZES the registries it boots into. The process-wide registries are
    shared by every test in the session and must stay unfrozen for the tests
    that register fakes into them, so any test here that enters the real app
    boots into private registries instead."""
    from narranexus.kernel.plugins.registries import Registries
    from narranexus.platform.module_system import plugins_boot

    monkeypatch.setattr(plugins_boot, "KERNEL_REGISTRIES", Registries())
    monkeypatch.setattr(plugins_boot, "_REPORTS", {})

