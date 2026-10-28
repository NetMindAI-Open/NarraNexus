"""
@file_name: test_step_1_greeting_seed.py
@author: Bin Liang
@date: 2026-08-20
@description: step_1_select_narrative must seed the bootstrap greeting exactly
ONCE — into the HEAD narrative's chat instance — even when selection returns
several narratives (first turn is non-continuous, BM25 top-k = 2-3). Seeding
per-narrative (the earlier bug) wrote 2-3 duplicate greetings on the first
screen; this locks the head-only behavior so a regression to per-narrative
seeding turns red.
"""
from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.agent_runtime._agent_runtime_steps.context import RunContext
from narranexus.platform.utils import utc_now

mod = importlib.import_module(
    "narranexus.platform.agent_runtime._agent_runtime_steps.step_1_select_narrative"
)


def _narrative(nid: str):
    return SimpleNamespace(
        id=nid,
        updated_at=None,
        narrative_info=SimpleNamespace(name=f"N-{nid}", current_summary="s"),
    )


def _selection(narratives):
    """Stand-in for `NarrativeSelectionResult`.

    `no_durable_topic` is not optional padding: `step_1_select_narrative` reads
    it unconditionally to set `ctx.no_durable_topic`, which decides whether §4.4
    freezes the narrative's retrieval surface for the turn. The real model
    defaults it to False, so production is safe; a double that omits it only
    breaks the test. Do not drop it, and do not "fix" the read site with a
    getattr default — that would hide a genuinely thin double next time.
    """
    return SimpleNamespace(
        narratives=narratives,
        scores={},
        selection_reason="bm25",
        selection_method="keyword",
        is_new=False,
        retrieval_method="keyword",
        no_durable_topic=False,
    )


@pytest.mark.asyncio
async def test_seeds_greeting_once_into_head_narrative(monkeypatch):
    narratives = [_narrative("n1"), _narrative("n2"), _narrative("n3")]

    narrative_service = SimpleNamespace(
        select=AsyncMock(return_value=_selection(narratives)),
        load_narrative_from_db=AsyncMock(return_value=None),
    )
    session_service = SimpleNamespace(save_session=AsyncMock())

    # Each narrative gets its own chat instance (the multi-instance condition).
    monkeypatch.setattr(
        mod,
        "_ensure_user_chat_instance",
        AsyncMock(side_effect=lambda aid, uid, nid: f"chat_{nid}"),
    )
    # Agent is bootstrapping → a greeting is resolved.
    monkeypatch.setattr(
        "narranexus.platform.bootstrap.greeting_seed.resolve_bootstrap_greeting_to_seed",
        AsyncMock(return_value="Hello there!"),
    )
    seed_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "narranexus_plugins.chat_module.seed_bootstrap_greeting", seed_spy
    )
    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client",
        AsyncMock(return_value=object()),
    )

    ctx = RunContext(
        agent_id="agent_a",
        user_id="user_u",
        input_content="hi",
        working_source="chat",
        event=SimpleNamespace(created_at=utc_now()),
        session=SimpleNamespace(session_id="s1"),
    )

    async for _ in mod.step_1_select_narrative(ctx, narrative_service, session_service):
        pass

    # Exactly one greeting seeded, and into the HEAD (narrative_list[0]) instance.
    assert seed_spy.await_count == 1, f"expected 1 seed, got {seed_spy.await_count}"
    seeded_instance = seed_spy.await_args.args[3]
    assert seeded_instance == "chat_n1", f"seeded non-head instance: {seeded_instance}"


def test_seed_is_not_wired_into_per_narrative_ensure():
    """Structural guard for round-1 Critical 1: the seed must NOT live inside
    _ensure_user_chat_instance (which runs once PER narrative → 2-3 duplicate
    greetings). The await_count assertion above mocks that function out, so this
    source check is what actually catches a regression that moves the seed back
    inside it."""
    src = inspect.getsource(mod._ensure_user_chat_instance)
    assert "seed_bootstrap_greeting" not in src
    assert "resolve_bootstrap_greeting_to_seed" not in src


@pytest.mark.asyncio
async def test_no_seed_when_not_bootstrapping(monkeypatch):
    narratives = [_narrative("n1")]
    narrative_service = SimpleNamespace(
        select=AsyncMock(return_value=_selection(narratives)),
        load_narrative_from_db=AsyncMock(return_value=None),
    )
    session_service = SimpleNamespace(save_session=AsyncMock())

    monkeypatch.setattr(
        mod,
        "_ensure_user_chat_instance",
        AsyncMock(side_effect=lambda aid, uid, nid: f"chat_{nid}"),
    )
    monkeypatch.setattr(
        "narranexus.platform.bootstrap.greeting_seed.resolve_bootstrap_greeting_to_seed",
        AsyncMock(return_value=None),  # not a bootstrap agent
    )
    seed_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "narranexus_plugins.chat_module.seed_bootstrap_greeting", seed_spy
    )
    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client",
        AsyncMock(return_value=object()),
    )

    ctx = RunContext(
        agent_id="agent_a",
        user_id="user_u",
        input_content="hi",
        working_source="chat",
        event=SimpleNamespace(created_at=utc_now()),
        session=SimpleNamespace(session_id="s1"),
    )

    async for _ in mod.step_1_select_narrative(ctx, narrative_service, session_service):
        pass

    seed_spy.assert_not_awaited()
