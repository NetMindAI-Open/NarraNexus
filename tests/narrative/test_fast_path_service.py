"""
@file_name: test_fast_path_service.py
@date: 2026-08-14
@description: Real-service locks for the fast-path narrative primitives.

The step-level tests all inject AsyncMocks, which is how the small-corpus
fragmentation bug slipped through review. These run the REAL
NarrativeService against the isolated test DB and lock:

* create_fast persists a loadable narrative carrying the BM25 routing
  surface (title from the query, keywords, topic hint).
* The small-corpus regime is real: right after creation, the verbatim
  same query can still miss select_fast (BM25 IDF is degenerate on a
  tiny corpus) — which is exactly why step_1_fast_select must reuse the
  session thread instead of creating again (locked at the step level in
  test_step_1_fast_select.py).
"""
from __future__ import annotations

import pytest

from narranexus.platform.narrative import NarrativeService

AGENT = "agent_fastsvc"
USER = "user_fastsvc"


def _const(v):
    async def _f(*a, **k):
        return v

    return _f


@pytest.fixture
def service(db_client, monkeypatch):
    svc = NarrativeService(agent_id=AGENT, database_client=db_client)
    svc._crud.set_database_client(db_client)
    svc._retrieval.set_database_client(db_client)
    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client",
        _const(db_client),
    )
    return svc


@pytest.mark.asyncio
async def test_create_fast_persists_with_routing_surface(service):
    query = "please help me renew my passport before the trip"
    narrative = await service.create_fast(AGENT, USER, query)

    loaded = await service.load_narrative_from_db(narrative.id)
    assert loaded is not None
    assert loaded.id == narrative.id
    assert query.startswith(loaded.narrative_info.name[:10])
    assert narrative.topic_keywords


@pytest.mark.asyncio
async def test_select_fast_empty_corpus_is_a_clean_miss(service):
    res = await service.select_fast(AGENT, USER, "ok great")
    assert res.narrative is None
    assert res.top1_raw is None


@pytest.mark.asyncio
async def test_audit_fast_persists_top1_raw(service, db_client):
    # _write_audit swallows every failure by design, so only a
    # persisted-row assertion can catch a mapping regression — the
    # object-level kwargs assert in the step tests cannot.
    await service.audit_fast(
        AGENT,
        USER,
        "calibration probe",
        retrieval_method="bm25_fast_override",
        chosen_narrative_id="nar_x",
        trigger="chat",
        is_user_chat=True,
        keyword_ms=7,
        top1_raw=13.75,
    )
    row = await db_client.get_one(
        "narrative_routing_audit", {"agent_id": AGENT, "user_id": USER}
    )
    assert row is not None
    assert row["gate_top1_raw"] == 13.75
    assert row["retrieval_method"] == "bm25_fast_override"
    assert row["selection_method"] == "fast"
    assert row["keyword_ms"] == 7
    assert row["continuity_ms"] is None


@pytest.mark.asyncio
async def test_select_fast_hit_is_the_created_narrative(service):
    created = await service.create_fast(
        AGENT, USER, "renew my passport before the trip"
    )
    res = await service.select_fast(
        AGENT, USER, "renew my passport before the trip"
    )
    # Small-corpus BM25 may legitimately miss even the verbatim query
    # (IDF degeneracy) — that reality is exactly why the step layer's
    # anchor-reuse branch exists. But when it DOES pick, it must be the
    # right row.
    if res.narrative is not None:
        assert res.narrative.id == created.id


@pytest.mark.asyncio
async def test_select_fast_anchor_override_uses_strong_floor(service, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    # A mid-strength score (above the noise floor, below the override
    # floor) picks normally but must NOT be allowed to steal a live
    # anchor — the step layer reuses the anchor. Thresholds in config.py.
    from narranexus.platform.narrative.config import config

    score = (config.NARRATIVE_MATCH_RAW_FLOOR + config.FAST_ANCHOR_OVERRIDE_FLOOR) / 2

    async def _fake_search(**_kw):
        return [SimpleNamespace(narrative_id="n1", raw_score=score)]

    monkeypatch.setattr(service._retrieval, "keyword_search", _fake_search)
    loaded = SimpleNamespace(id="n1")
    monkeypatch.setattr(service._crud, "load_by_id", AsyncMock(return_value=loaded))

    plain = await service.select_fast(AGENT, USER, "q")
    assert plain.narrative is loaded
    assert plain.top1_raw == score

    guarded = await service.select_fast(AGENT, USER, "q", against_live_anchor=True)
    assert guarded.narrative is None
    assert guarded.top1_raw == score
