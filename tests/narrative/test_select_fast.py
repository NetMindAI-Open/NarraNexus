"""
@file_name: test_select_fast.py
@date: 2026-08-06
@description: NarrativeService.select_fast — BM25 top-1 probe.

Locks:
- Zero LLM, zero creation, zero session writes: the method only calls
  the BM25 keyword search (top_k=1) and, on a decisive pick, CRUD
  load_by_id.
- 2026-08-14 (PR #307): returns a FastSelectResult. No candidate ->
  narrative None; sub-floor top-1 -> narrative None but the raw score
  still rides out (audit calibration); vanished row -> narrative None
  (no crash).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.narrative.narrative_service import NarrativeService


def _service_with_fakes(search_results, loaded):
    service = NarrativeService.__new__(NarrativeService)
    service._retrieval = SimpleNamespace(
        keyword_search=AsyncMock(return_value=search_results)
    )
    service._crud = SimpleNamespace(load_by_id=AsyncMock(return_value=loaded))
    return service


@pytest.mark.asyncio
async def test_top1_hit_loads_and_returns_narrative():
    hit = SimpleNamespace(narrative_id="nar_1", raw_score=5.0)
    narrative = SimpleNamespace(id="nar_1")
    service = _service_with_fakes([hit], narrative)

    result = await service.select_fast("agent_a", "user_u", "weather query")

    assert result.narrative is narrative
    assert result.top1_raw == 5.0
    service._retrieval.keyword_search.assert_awaited_once_with(
        query="weather query", user_id="user_u", agent_id="agent_a", top_k=1
    )
    service._crud.load_by_id.assert_awaited_once_with("nar_1")


@pytest.mark.asyncio
async def test_no_candidates_is_a_bare_probe():
    service = _service_with_fakes([], None)
    result = await service.select_fast("a", "u", "q")
    assert result.narrative is None
    assert result.top1_raw is None
    service._crud.load_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_vanished_row_yields_no_narrative():
    hit = SimpleNamespace(narrative_id="nar_gone", raw_score=9.0)
    service = _service_with_fakes([hit], None)
    result = await service.select_fast("a", "u", "q")
    assert result.narrative is None
    # The score still rides out — the miss was the row, not the match.
    assert result.top1_raw == 9.0


@pytest.mark.asyncio
async def test_below_floor_score_is_a_miss_but_score_is_reported():
    """Review finding #7 (PR #294): the full path gates weak BM25 matches
    behind an LLM tier; the fast path has no LLM, so the raw floor applies
    directly — a one-word accidental overlap must not become the turn's
    background narrative. Sub-floor top-1 = no pick, but the raw score is
    still reported for audit calibration (PR #307 round 2)."""
    weak = SimpleNamespace(narrative_id="nar_weak", raw_score=1.0)
    service = _service_with_fakes([weak], SimpleNamespace(id="nar_weak"))
    result = await service.select_fast("a", "u", "q")
    assert result.narrative is None
    assert result.top1_raw == 1.0
    service._crud.load_by_id.assert_not_awaited()
