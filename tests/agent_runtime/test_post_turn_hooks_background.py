"""
@file_name: test_post_turn_hooks_background.py
@date: 2026-08-14
@description: Steps 5-6 run detached — the contract nobody had pinned.

PRD "Team chat responsiveness" acceptance #3 is two claims, and only the first half
was ever asserted anywhere: "the reply does not wait for the post-turn hooks"
AND "the memory / social / narrative writes still complete correctly once they
run in the background". The second half has been load-bearing since 2026-05
and had no test at all.

What is pinned here:

* run() returns while a slow hook is still going (non-blocking, for real —
  measured against the hook, not against a sleep),
* the hook's writes land after ``drain_background_tasks()``,
* one module blowing up does not take the turn's already-delivered reply with
  it, and the failure is logged rather than swallowed,
* a credential-class failure reaches ``alert_background_llm_failure`` — the
  2026-07 incident where the platform key 401'd for two weeks in silence,
* the owner's helper-LLM credentials are re-injected into the detached task,
  which is the whole reason ``inject_owner_helper_credentials`` is called
  inside ``_run_hooks_background`` rather than inherited from run().

The harness stubs Steps 1-2.5 (narrative selection and module loading each
call helper LLMs; neither is what this file is about) and runs ``silent=True``
so Step 3 is skipped by the production code path rather than by a patch.
Steps 0 and 4 run for real against the in-memory sqlite, so the Event row the
hooks read is a real one.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from loguru import logger

from narranexus.platform.agent_runtime.agent_runtime import AgentRuntime
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.utils.background_tasks import drain, pending


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_get_db(monkeypatch, db_client):
    """Route every inner ``get_db_client()`` to the test's sqlite."""
    from narranexus.platform.utils.db import db_factory

    async def _fake_get_db():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", _fake_get_db)
    yield


@pytest.fixture(autouse=True)
def stub_preparation_steps(monkeypatch):
    """Neutralise Steps 1 / 1.5 / 2 / 2.5.

    Each of them calls a helper LLM. None of them is what this file tests, and
    letting them run would make every assertion here depend on narrative
    routing behaviour. Step 0 and Step 4 are left alone on purpose — the Event
    row the hooks operate on has to be real.
    """
    from narranexus.platform.agent_runtime import steps as ar

    async def _fake_step_1(ctx, narrative_service, session_service):
        ctx.narrative_list = []
        return
        yield  # pragma: no cover — makes this an async generator

    async def _fake_step_1_5(*a, **k):
        return None

    async def _fake_step_2(ctx):
        ctx.load_result = None
        ctx.module_list = []
        return
        yield  # pragma: no cover

    async def _fake_step_2_5(*a, **k):
        return
        yield  # pragma: no cover

    monkeypatch.setattr(ar, "step_1_select_narrative", _fake_step_1)
    monkeypatch.setattr(ar, "step_1_5_init_markdown", _fake_step_1_5)
    monkeypatch.setattr(ar, "step_2_load_modules", _fake_step_2)
    monkeypatch.setattr(ar, "step_2_5_sync_instances", _fake_step_2_5)
    yield


@pytest.fixture(autouse=True)
def patch_llm_config(monkeypatch):
    """A usable owner LLM config, so run() gets past its credential preflight."""
    from narranexus.platform.agent_framework import api_config

    async def _configs(_agent_id: str):
        return _FakeRuntimeConfigs()

    monkeypatch.setattr(
        api_config, "get_agent_owner_runtime_llm_configs", _configs, raising=False
    )
    yield


class _FakeRuntimeConfigs:
    """Duck-types just enough of RuntimeLLMConfigs for the preflight."""

    def __getattr__(self, _name):
        return None


@pytest.fixture(autouse=True)
def helper_injection_succeeds(monkeypatch):
    """Give the detached task a resolvable owner provider.

    Without this the seeded agent has no provider row, so
    ``inject_owner_helper_credentials`` raises ``ProviderResolverError`` and
    ``_run_hooks_background`` takes its early-return branch — Steps 5-6 never
    run at all and every assertion below reads as "the hook didn't fire". That
    early return is correct production behaviour (do not run LLM hooks against
    the platform key), it just is not what these tests are about.
    """
    async def _inject(_agent_id, _db):
        return "owner_1"

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.providers.resolver."
        "inject_owner_helper_credentials",
        _inject,
    )
    yield


@pytest.fixture(autouse=True)
async def no_task_leaks_between_tests():
    """Cancel anything this test left detached.

    Without it a task parked past its test keeps running into the NEXT one —
    against a db_client the previous fixture already closed — and every failure
    in this file reads as somebody else's bug.
    """
    yield
    leftovers = [t for t in pending() if t.get_name().startswith("post_turn_hooks:")]
    for t in leftovers:
        t.cancel()
    if leftovers:
        await asyncio.gather(*leftovers, return_exceptions=True)


@pytest.fixture
def log_lines():
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


class _RecordingHookManager:
    """Stands in for HookManager. Records calls; optionally blocks or raises.

    ``gate`` lets a test hold Step 5 open so it can assert run() already
    returned while the hook is demonstrably still in flight — the only honest
    way to test "does not block" without a sleep-and-hope.
    """

    def __init__(self, *, gate: asyncio.Event | None = None, raises: BaseException | None = None):
        self._gate = gate
        self._raises = raises
        self.persist_turn_calls = 0
        self.after_execution_calls = 0
        self.writes: list[str] = []

    async def persist_turn(self, module_list, params):
        self.persist_turn_calls += 1
        return None

    async def after_turn(self, module_list, params):
        self.after_execution_calls += 1
        if self._gate is not None:
            await self._gate.wait()
        if self._raises is not None:
            raise self._raises
        # Stand-in for the memory / social / narrative writes.
        self.writes.append("entity-summary")
        # Step 5 reports `len(callback_results)`, so a list is the contract.
        return []

    async def hook_callback_results(self, **kwargs):
        return None

    async def gather(self, *a, **k):
        return {}


async def _seed_agent(db, *, agent_id: str, created_by: str) -> None:
    await db.insert(
        "agents",
        {
            "agent_id": agent_id,
            "agent_name": agent_id,
            "created_by": created_by,
            "agent_type": "general",
            "is_public": 0,
        },
    )


async def _consume(gen: AsyncIterator) -> list:
    return [m async for m in gen]


async def _run_turn(db_client, hooks, *, agent_id="agent_bg") -> list:
    await _seed_agent(db_client, agent_id=agent_id, created_by="owner_1")
    # `database_client` injected on purpose: with it None, AgentRuntime owns
    # the client it resolves and closes it on exit — and since the fixture
    # patched `get_db_client` to hand back the TEST's client, that close takes
    # the test database down underneath the still-running background task.
    runtime = AgentRuntime(database_client=db_client, hook_manager=hooks)
    return await _consume(
        runtime.run(
            agent_id=agent_id,
            user_id="owner_1",
            input_content="post-turn hooks, please",
            working_source=WorkingSource.CHAT,
            silent=True,
        )
    )


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_returns_while_the_hooks_are_still_running(db_client):
    """Acceptance #3, first half — and asserted against the hook itself.

    The gate stays shut until run() has been fully consumed, so "did not
    block" is measured against the hook actually being stuck, not against a
    sleep that happens to be long enough.
    """
    gate = asyncio.Event()
    hooks = _RecordingHookManager(gate=gate)

    # Bounded: if Steps 5-6 ever go back to being awaited inline, run() cannot
    # return while the gate is shut. Without this budget that regression is a
    # CI hang — strictly worse than a CI failure, and much harder to read.
    try:
        await asyncio.wait_for(_run_turn(db_client, hooks), timeout=10.0)
    except asyncio.TimeoutError:
        gate.set()
        pytest.fail(
            "run() did not return while the post-turn hook was blocked — "
            "Steps 5-6 are being awaited inline again"
        )

    # run() is done. Let the detached task reach the hook — it has real awaits
    # before it (credential injection), so a single yield is not enough. This
    # loop can only ever finish early; it cannot mask a blocking Step 5,
    # because the gate below is still shut.
    for _ in range(200):
        if hooks.after_execution_calls:
            break
        await asyncio.sleep(0.005)

    assert hooks.after_execution_calls == 1
    assert hooks.writes == [], "the hook completed inline — the reply waited for it"
    assert any(t.get_name().startswith("post_turn_hooks:") for t in pending())

    gate.set()
    await drain(timeout=2.0)
    assert hooks.writes == ["entity-summary"]


@pytest.mark.asyncio
async def test_the_writes_still_land_once_the_background_task_runs(db_client):
    """Acceptance #3, second half — the part that had no test.

    Backgrounding is only correct if the work actually happens. Without this,
    'we moved it off the critical path' and 'we dropped it' look identical.
    """
    hooks = _RecordingHookManager()

    await _run_turn(db_client, hooks)
    await drain(timeout=2.0)

    assert hooks.after_execution_calls == 1
    assert hooks.writes == ["entity-summary"]


@pytest.mark.asyncio
async def test_a_failing_hook_does_not_retract_the_delivered_reply(db_client, log_lines):
    """The turn is already on the user's screen when Steps 5-6 run.

    A hook exploding afterwards must not corrupt what was persisted for that
    turn, and must not be swallowed (lesson #3).
    """
    hooks = _RecordingHookManager(raises=RuntimeError("hook exploded"))

    await _run_turn(db_client, hooks)
    await drain(timeout=2.0)

    events = await db_client.get("events", {"agent_id": "agent_bg"})
    assert len(events) == 1, "the failed hook disturbed the turn's own event row"

    joined = "\n".join(log_lines)
    assert "hook exploded" in joined, "the background failure was swallowed"
    assert "[BG] Steps 5-6 failed" in joined


@pytest.mark.asyncio
async def test_a_credential_failure_alerts_the_owner(db_client, monkeypatch):
    """The 2026-07 shape: the key is dead, and long memory degrades silently.

    ``alert_background_llm_failure`` is the mechanism that was added so this
    could not recur; nothing asserted that the post-turn path reaches it.
    """
    from narranexus.platform.agent_runtime import agent_runtime as ar

    alerts: list[dict] = []

    async def _fake_alert(**kwargs):
        alerts.append(kwargs)

    monkeypatch.setattr(
        "narranexus.platform.services.background_llm_alerts.alert_background_llm_failure",
        _fake_alert,
    )

    class _AuthError(Exception):
        pass

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.llm.failure.is_credential_error",
        lambda e: isinstance(e, _AuthError),
    )
    assert ar is not None  # import kept meaningful for the reader

    hooks = _RecordingHookManager(raises=_AuthError("401 invalid api key"))
    await _run_turn(db_client, hooks)
    await drain(timeout=2.0)

    assert len(alerts) == 1, "a credential-class hook failure did not alert"
    assert alerts[0]["agent_id"] == "agent_bg"
    assert alerts[0]["source"] == "post_turn_hooks"
    assert alerts[0]["owner_user_id"] == "owner_1", (
        "the alert must carry the owner, or it never reaches an inbox "
        "(the exact regression fixed on 2026-07-07)"
    )


@pytest.mark.asyncio
async def test_the_detached_task_reinjects_the_owner_helper_llm(db_client, monkeypatch):
    """A detached task does NOT inherit the run's per-turn ContextVars.

    That is why ``inject_owner_helper_credentials`` is called INSIDE
    ``_run_hooks_background``. Drop that call and Step-5's LLM hooks quietly
    fall back to the platform key — which is how the 2026-07 incident started.
    """
    injected: list[str] = []

    async def _fake_inject(agent_id, _db):
        injected.append(agent_id)
        return "owner_1"

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.providers.resolver."
        "inject_owner_helper_credentials",
        _fake_inject,
    )

    hooks = _RecordingHookManager()
    await _run_turn(db_client, hooks)
    await drain(timeout=2.0)

    assert injected == ["agent_bg"], (
        "the background task ran without re-injecting the owner's helper LLM"
    )
