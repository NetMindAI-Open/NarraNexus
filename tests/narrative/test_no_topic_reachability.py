"""
@file_name: test_no_topic_reachability.py
@date: 2026-08-16
@description: The "no durable topic" verdict must be REACHABLE and its landing
              must be CALLABLE. Both were neither, and the first test suite
              missed both because it stubbed the wrong boundary.

Live probe on 2026-08-16 (real DB, real helper LLM, agent_d8795abf5021) found:

  A. An empty candidate pool short-circuited `llm_judge_unified` before the
     model was ever asked, so `matched_type` came back None rather than
     "no_topic" and the landing rule never ran. A bare "哈哈哈" therefore
     OPENED A NEW THREAD while the session held a perfectly good anchor —
     precisely the fragmentation plan 4-A' exists to prevent — and an
     ephemeral (voice) turn created a narrative, breaking its no-trace
     contract. The early return was dead code while eight default buckets
     were always in the menu; removing them from the menu ACTIVATED it.

  B. `_land_no_topic_turn` called `create_from_query` without the required
     `narrative_type`, so the create branch raised TypeError on every real
     turn that reached it.

Why the first suite passed anyway, and what these tests do differently:

  * it stubbed `_llm_judge_unified` — OUR function — so the early return
    inside it was never executed. Here we stub `get_helper_sdk`, i.e. the
    network edge, and let the real judge code run.
  * it hand-wrote a `create_from_query` double with the wrong signature,
    turning the bug into the fixture's definition of correct. Here the double
    is `create_autospec`'d from the real method, so a mis-shaped call fails.

The rule this encodes: stub the boundary you do not own, never the logic
under test.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest

from narranexus.platform.narrative._narrative_impl import _retrieval_llm
from narranexus.platform.narrative._narrative_impl.retrieval import NarrativeRetrieval
from narranexus.platform.narrative.models import NarrativeType


def _fake_sdk(monkeypatch, *, category: str, calls: list):
    """Stub the helper-LLM edge only — the real judge code still runs."""

    class _SDK:
        async def llm_function(self, *, instructions, user_input, output_type,
                              **kwargs):
            calls.append({"instructions": instructions, "user_input": user_input})
            return SimpleNamespace(
                final_output=output_type(
                    reason="stubbed model answer",
                    matched_category=category,
                    matched_index=-1,
                )
            )

    monkeypatch.setattr(_retrieval_llm, "get_helper_sdk", lambda: _SDK())
    return calls


# ===================================================================== #
# A · the verdict must be reachable with an empty pool                  #
# ===================================================================== #


@pytest.mark.asyncio
async def test_judge_is_asked_even_with_no_candidates(monkeypatch):
    """A contentless greeting has zero term overlap, so the pool is empty —
    which is exactly when we most need the verdict. "No candidates" is not an
    answer to "does this turn carry a durable topic"."""
    calls = _fake_sdk(monkeypatch, category="no_durable_topic", calls=[])

    result = await _retrieval_llm.llm_judge_unified(
        query="哈哈哈", search_candidates=[], default_candidates=[],
        participant_candidates=[],
    )

    assert calls, "the model must be consulted, not short-circuited"
    assert result["matched_type"] == "no_topic"
    assert result["matched_id"] is None


@pytest.mark.asyncio
async def test_empty_pool_still_allows_a_new_topic_verdict(monkeypatch):
    """"No candidates" must not collapse into "no durable topic" either — a
    substantive first-ever message deserves its own thread."""
    _fake_sdk(monkeypatch, category="none", calls=[])

    result = await _retrieval_llm.llm_judge_unified(
        query="帮我把 Lark bot 重新绑定一下新 App",
        search_candidates=[], default_candidates=[], participant_candidates=[],
    )

    assert result["matched_type"] is None
    assert result["matched_id"] is None


@pytest.mark.asyncio
async def test_empty_pool_is_stated_to_the_model(monkeypatch):
    """With nothing to match against, the prompt must say so. Otherwise the
    model is asked to choose from a list that was never rendered."""
    calls = _fake_sdk(monkeypatch, category="no_durable_topic", calls=[])

    await _retrieval_llm.llm_judge_unified(
        query="嗯嗯", search_candidates=[], default_candidates=[],
        participant_candidates=[],
    )

    assert "Existing Topics" in calls[0]["user_input"]


@pytest.mark.asyncio
async def test_retrieval_returns_the_verdict_instead_of_creating(monkeypatch):
    """End of the chain: with an empty pool and a no-topic verdict, the
    retrieval tier must hand the decision UP, not create a narrative.

    This is the assertion that would have caught the live failure: the tier
    created a thread for "哈哈哈" while the session had an anchor."""
    _fake_sdk(monkeypatch, category="no_durable_topic", calls=[])

    retrieval = NarrativeRetrieval.__new__(NarrativeRetrieval)
    retrieval.agent_id = "agent_x"
    retrieval._crud = SimpleNamespace(load_by_id=AsyncMock(return_value=None))
    creator = create_autospec(NarrativeRetrieval, instance=True).create_from_query
    retrieval.create_from_query = creator

    async def _fake_db():
        return SimpleNamespace()

    monkeypatch.setattr(
        "narranexus.platform.narrative._narrative_impl.retrieval.get_db_client",
        _fake_db,
    )
    # Imported lazily inside the function, so patch it where it lives.
    monkeypatch.setattr(
        "narranexus.platform.repository.NarrativeRepository",
        lambda db: SimpleNamespace(get_default_narratives=AsyncMock(return_value=[])),
    )

    result = await retrieval._llm_unified_match(
        query="哈哈哈", search_results=[], agent_id="agent_x", user_id="user_x",
        top_k=3, narrative_type=NarrativeType.CHAT, best_score=None,
        audit=SimpleNamespace(judge_ran=False, judge_category=None,
                              judge_matched_id=None, judge_reason=None),
    )

    assert result.no_durable_topic is True
    assert result.narratives == []
    creator.assert_not_called()


# ===================================================================== #
# B · the landing must be callable against the real signature           #
# ===================================================================== #


@pytest.mark.asyncio
async def test_create_branch_calls_create_from_query_correctly(monkeypatch):
    """`create_autospec` binds against the REAL method, so a missing
    `narrative_type` fails here exactly as it failed on the live machine.

    The first suite hand-wrote this double with three parameters, which made
    the wrong call shape the fixture's definition of correct.
    """
    from datetime import datetime, timezone

    from narranexus.platform.narrative.models import Narrative, NarrativeInfo
    from narranexus.platform.narrative.narrative_service import NarrativeService

    now = datetime.now(timezone.utc)
    created = Narrative(
        id="nar_new", type=NarrativeType.CHAT, agent_id="agent_x",
        narrative_info=NarrativeInfo(name="你好", description="",
                                     current_summary="", actors=[]),
        event_ids=[], is_special="other", created_at=now, updated_at=now,
    )

    creator = create_autospec(NarrativeRetrieval, instance=True).create_from_query
    creator.return_value = created

    svc = NarrativeService.__new__(NarrativeService)
    svc._crud = SimpleNamespace(load_by_id=AsyncMock(return_value=None))
    svc._retrieval = SimpleNamespace(create_from_query=creator)

    landing = await svc._land_no_topic_turn(
        agent_id="agent_x", user_id="user_x", query_text="你好",
        session=None, reason="stub",
    )
    narratives, method, reason, is_new = (
        landing.narratives, landing.method, landing.reason, landing.is_new
    )

    assert is_new is True and [n.id for n in narratives] == ["nar_new"]
    assert method == "new_created"
    # autospec would already have raised on a mis-shaped call; assert the
    # required argument is genuinely supplied rather than defaulted away.
    assert creator.await_args.kwargs["narrative_type"] is NarrativeType.CHAT
