"""
@file_name: test_consolidation_worker_cost.py
@author: NarraNexus
@date: 2026-07-03
@description: Phase 0 (module H) — the consolidation worker must account the
LLM tokens its background passes burn.

Before the fix the worker ran OUTSIDE any cost context (only AgentRuntime.run
set it), so every consolidation LLM call recorded ZERO. These tests verify the
worker now:
  1. sets the cost context to (agent_id, db) for the duration of a pass,
  2. never sets a current_user_id — so record_cost's deduct hook can NEVER
     fire (records the usage, never bills the owner's free tier; iron rule #15),
  3. always clears the context in `finally` (no cross-tenant bleed), even when
     consolidation raises.

The "record but never deduct" guarantee is closed together with
tests/utils/test_cost_tracker_deduct_hook.py::test_no_deduct_when_user_id_missing,
which asserts record_cost does not deduct when current_user_id is None.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework.api_config import (
    get_current_user_id,
    set_current_user_id,
    set_provider_source,
)
from xyz_agent_context.memory.spec import get_spec
from xyz_agent_context.utils.cost_tracker import (
    clear_cost_context,
    get_cost_context,
)

from unittest.mock import patch


_CAPTURED: dict = {}


class _FakeRecord:
    def __init__(self, subtype: str, rid: str):
        self.subtype = subtype
        self.record_id = rid
        self.created_at = None
        self.content_text = "x"


class _FakeRepo:
    def __init__(self, facts):
        self._facts = facts
        self.tombstoned: list = []

    async def query(self, **_kw):
        return self._facts

    async def tombstone(self, rid):
        self.tombstoned.append(rid)


class _FakeEngine:
    """Stands in for MemoryEngine: repo returns one raw fact so the worker
    reaches consolidate(), which snapshots the ambient cost/user context."""

    def __init__(self, db, agent_id):
        self._db = db
        self.agent_id = agent_id

    def repo(self, kind):
        subtype = get_spec(kind).subtypes[0]
        return _FakeRepo([_FakeRecord(subtype, "rec1")])

    async def consolidate(self, kind, *, scope_type, scope_id, new_facts, existing):
        _CAPTURED["cost_ctx"] = get_cost_context()
        _CAPTURED["user_id"] = get_current_user_id()
        return len(new_facts)


class _BoomEngine(_FakeEngine):
    async def consolidate(self, *_a, **_k):
        raise RuntimeError("llm down")


@pytest.fixture(autouse=True)
def _reset_ctx():
    clear_cost_context()
    set_current_user_id(None)
    set_provider_source(None)
    _CAPTURED.clear()
    yield
    clear_cost_context()
    set_current_user_id(None)
    set_provider_source(None)


def _worker(db_client):
    from xyz_agent_context.services import memory_consolidation_worker as mod
    return mod, mod.MemoryConsolidationWorker(db_client=db_client)


@pytest.mark.asyncio
async def test_worker_sets_cost_context_carrying_agent_and_db(db_client):
    mod, worker = _worker(db_client)
    with patch.object(mod, "MemoryEngine", _FakeEngine):
        result = await worker._default_engine_consolidate(
            agent_id="agt_cost", scope_type="agent", scope_id="", kind="observation",
        )
    assert result == 1
    # The consolidation LLM call now runs inside a live cost context…
    assert _CAPTURED["cost_ctx"] == ("agt_cost", db_client)
    # …but NEVER with a current_user_id → the deduct hook cannot fire.
    assert _CAPTURED["user_id"] is None


@pytest.mark.asyncio
async def test_worker_clears_cost_context_after_success(db_client):
    mod, worker = _worker(db_client)
    with patch.object(mod, "MemoryEngine", _FakeEngine):
        await worker._default_engine_consolidate(
            agent_id="agt1", scope_type="agent", scope_id="", kind="observation",
        )
    assert get_cost_context() is None  # no bleed into the next scope/tenant


@pytest.mark.asyncio
async def test_worker_clears_cost_context_even_on_failure(db_client):
    mod, worker = _worker(db_client)
    with patch.object(mod, "MemoryEngine", _BoomEngine):
        with pytest.raises(RuntimeError):
            await worker._default_engine_consolidate(
                agent_id="agt1", scope_type="agent", scope_id="", kind="observation",
            )
    assert get_cost_context() is None  # finally clears on the error path too
