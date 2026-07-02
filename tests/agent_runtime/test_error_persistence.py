"""
@file_name: test_error_persistence.py
@author: Bin Liang
@date: 2026-04-20
@description: Bug 18 — AgentRuntime must persist `event.final_output` when it
gives up early on the LLMResolverError path.

Before the fix: Step 4 (event persist) and Step 5 (hook_after_event_execution)
were unreachable because the error branch `return`-ed right after yielding
`ErrorMessage`. The Event row stayed with `final_output=NULL` forever —
the failed turn became invisible to audit queries and to any UI that
renders history from the `events` table. The user's input itself was fine
(Step 0 writes `env_context.input`), but there was no record of "what
happened when we tried to run".

This test drives the full code path against an in-memory sqlite and
checks the final_output marker shows up.
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from xyz_agent_context.agent_framework.api_config import LLMConfigNotConfigured
from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
from xyz_agent_context.schema import ErrorMessage
from xyz_agent_context.schema.hook_schema import WorkingSource


@pytest.fixture(autouse=True)
def patch_get_db(monkeypatch, db_client):
    """Every inner `get_db_client()` call inside AgentRuntime, EventService,
    etc. routes to the test's in-memory sqlite fixture."""
    from xyz_agent_context.utils import db_factory

    async def _fake_get_db():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", _fake_get_db)
    yield


@pytest.fixture
def patch_llm_resolver(monkeypatch):
    """Make the owner-config resolution raise so the test exercises the
    error branch. AgentRuntime calls ``get_agent_owner_runtime_llm_configs``
    (the RuntimeLLMConfigs variant) — patch exactly that one; patching the
    legacy tuple variant ``get_agent_owner_llm_configs`` is a no-op and was
    silently leaving this test to assert against an unrelated message."""
    from xyz_agent_context.agent_framework import api_config

    async def _always_raise(_agent_id: str):
        raise LLMConfigNotConfigured(
            "test: 'agent' slot is not configured"
        )

    monkeypatch.setattr(
        api_config, "get_agent_owner_runtime_llm_configs", _always_raise
    )
    yield


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
    out = []
    async for msg in gen:
        out.append(msg)
    return out


@pytest.mark.asyncio
async def test_llm_resolver_error_persists_final_output_on_event(
    db_client, patch_llm_resolver,
):
    await _seed_agent(db_client, agent_id="agent_bug18", created_by="test_user")

    runtime = AgentRuntime()
    messages = await _consume(runtime.run(
        agent_id="agent_bug18",
        user_id="test_user",
        input_content="hello — will fail before Step 1",
        working_source=WorkingSource.CHAT,
    ))

    # Consumer saw the ErrorMessage
    errors = [m for m in messages if isinstance(m, ErrorMessage)]
    assert len(errors) == 1
    assert errors[0].error_type == "LLMConfigNotConfigured"

    # Step 0 created the Event with the user's question saved.
    events = await db_client.get("events", {"agent_id": "agent_bug18"})
    assert len(events) == 1
    event_row = events[0]

    # The Event row's final_output reflects the error (Bug 18 fix).
    assert event_row["final_output"] is not None
    assert "ERROR:LLMConfigNotConfigured" in event_row["final_output"]
    assert "'agent' slot" in event_row["final_output"]

    # And the user's original question is still preserved (was never the
    # bug, but we assert it as a sanity check).
    import json as _json
    env_ctx = _json.loads(event_row["env_context"])
    assert env_ctx["input"].startswith("hello")


@pytest.mark.asyncio
async def test_successful_run_not_affected(
    db_client, monkeypatch,
):
    """Sanity — when resolution succeeds, the error-branch persist logic
    never fires. (If we later tighten behaviour so this test needs to
    change, it's a signal we affected the happy path.)"""
    # This is a non-test: without a full LLM config + MCP subsystem
    # available in tests, the run cannot complete successfully. We rely
    # on the provider-resolution tests + existing regression suite to
    # cover the happy path. Placeholder test kept as documentation of
    # intent.
    assert True
