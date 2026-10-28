"""
@file_name: conftest.py
@date: 2026-08-16
@description: Fixtures for the default-bucket governance batch.

The three doubles here stub only the DB and LLM edges. Everything between
the entry point and those edges is the real production code — the batch is
about routing DECISIONS, and a double that reimplements the decision would
assert its own behaviour.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.narrative._narrative_impl.retrieval import NarrativeRetrieval
from narranexus.platform.narrative.narrative_service import NarrativeService


def _narrative(nid: str, *, name: str, is_special: str = "other", summary: str = ""):
    """A REAL Narrative, not a stand-in.

    NarrativeSelectionResult validates its list, and the pool builder calls
    ``searchable_text()`` — a double would only prove the double behaves.
    """
    from datetime import datetime, timezone

    from narranexus.platform.narrative.models import (
        Narrative,
        NarrativeInfo,
        NarrativeType,
    )

    now = datetime.now(timezone.utc)
    return Narrative(
        id=nid,
        type=NarrativeType.CHAT,
        agent_id="agent_x",
        narrative_info=NarrativeInfo(
            name=name, description="", current_summary=summary, actors=[]
        ),
        event_ids=[],
        is_special=is_special,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def retrieval_with_pool(monkeypatch):
    """A retrieval whose (agent,user) owns one real thread and one bucket."""
    real = _narrative("nar_real", name="部署脚本报错排查")
    bucket = _narrative(
        "nar_bucket", name="GreetingAndCourtesy", is_special="default"
    )

    spy = SimpleNamespace(seeded=[])

    retrieval = NarrativeRetrieval.__new__(NarrativeRetrieval)
    retrieval.agent_id = "agent_x"
    retrieval._crud = SimpleNamespace(
        load_by_agent_user=AsyncMock(return_value=[real, bucket]),
        load_by_id=AsyncMock(side_effect=lambda nid: {"nar_real": real}.get(nid)),
    )

    async def _seed(agent_id, user_id=None, crud=None):
        spy.seeded.append((agent_id, user_id))
        return {}

    monkeypatch.setattr(
        "narranexus.platform.narrative._narrative_impl.retrieval."
        "ensure_default_narratives",
        _seed,
    )

    async def _fake_db():
        return SimpleNamespace()

    monkeypatch.setattr(
        "narranexus.platform.narrative._narrative_impl.retrieval.get_db_client",
        _fake_db,
    )
    return retrieval, spy


@pytest.fixture
def judge_spy(monkeypatch, retrieval_with_pool):
    """Captures the candidate lists handed to the judge."""
    retrieval, spy = retrieval_with_pool
    spy.default_candidates = None
    spy.audit = SimpleNamespace(judge_ran=False, judge_category=None,
                                judge_matched_id=None, judge_reason=None)

    async def _capture(**kwargs):
        spy.default_candidates = kwargs.get("default_candidates")
        spy.search_candidates = kwargs.get("search_candidates")
        return {"matched_id": None, "matched_type": "no_topic", "reason": "stub"}

    retrieval._llm_judge_unified = _capture
    retrieval._get_participant_narratives = AsyncMock(return_value=[])
    return retrieval, spy


def _service(spy):
    svc = NarrativeService.__new__(NarrativeService)
    svc._continuity_detector = None
    svc._crud = SimpleNamespace(
        load_by_id=AsyncMock(side_effect=lambda nid: spy.rows.get(nid))
    )
    svc._write_audit = AsyncMock()
    return svc


@pytest.fixture
def service_no_topic(monkeypatch):
    """A service whose retrieval tier always answers 'no durable topic'."""
    real = _narrative("nar_real", name="部署脚本报错排查")
    created = _narrative("nar_created", name="你好")
    spy = SimpleNamespace(
        anchor=None,
        rows={"nar_real": real, "nar_created": created},
        created=[],
    )
    spy.session = SimpleNamespace(
        last_query="previous message", last_response="previous reply",
        current_narrative_id=None, query_count=0,
        last_query_time=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )

    svc = _service(spy)

    # No live helper LLM in a unit test: without this the continuity tier ran
    # for real (the fixture sets last_query/last_response), so the suite was
    # quietly billing a provider and inheriting its flakiness.
    class _NotContinuous:
        async def detect(self, **kwargs):
            return SimpleNamespace(is_continuous=False, confidence=1.0,
                                   reason="stubbed: not continuous")

    svc._get_continuity_detector = lambda: _NotContinuous()

    async def _retrieve(**kwargs):
        from narranexus.platform.narrative.models import NarrativeSelectionResult

        return NarrativeSelectionResult(
            narratives=[], selection_reason="stub", selection_method="no_topic",
            is_new=False, no_durable_topic=True,
        )

    # Autospec'd off the REAL method, not hand-written: a hand-written double
    # with the wrong parameter list is how Bug B (missing `narrative_type`)
    # survived a fully green suite on 2026-08-16. The double must fail when the
    # caller is wrong, not agree with it.
    from unittest.mock import create_autospec

    creator = create_autospec(NarrativeRetrieval, instance=True).create_from_query

    async def _record(*a, **kw):
        spy.created.append(kw.get("query"))
        return created

    creator.side_effect = _record
    creator.return_value = created

    svc._retrieval = SimpleNamespace(
        retrieve_top_k=_retrieve, create_from_query=creator
    )

    def _sync_anchor(*_a, **_k):
        spy.session.current_narrative_id = spy.anchor

    original_select = svc.select

    async def select(**kwargs):
        _sync_anchor()
        return await original_select(**kwargs)

    svc.select = select
    return svc, spy


@pytest.fixture
def service_continuity(monkeypatch):
    """A service with a stubbed continuity detector, for slice 5."""
    real = _narrative("nar_real", name="部署脚本报错排查")
    bucket = _narrative("nar_bucket", name="GreetingAndCourtesy",
                        is_special="default")
    spy = SimpleNamespace(
        real=real, bucket=bucket, anchor_narrative=real,
        continuity_verdict=False, continuity_detector_called=False,
        rows={"nar_real": real, "nar_bucket": bucket},
    )
    spy.session = SimpleNamespace(
        last_query="previous message", last_response="previous reply",
        current_narrative_id="nar_real", query_count=0,
        last_query_time=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )

    svc = _service(spy)

    class _Detector:
        async def detect(self, **kwargs):
            spy.continuity_detector_called = True
            return SimpleNamespace(
                is_continuous=spy.continuity_verdict, confidence=0.9,
                reason="stub",
            )

    svc._get_continuity_detector = lambda: _Detector()

    async def _retrieve(**kwargs):
        from narranexus.platform.narrative.models import NarrativeSelectionResult

        return NarrativeSelectionResult(
            narratives=[real], selection_reason="stub",
            selection_method="llm_confirmed", is_new=False,
        )

    svc._retrieval = SimpleNamespace(retrieve_top_k=_retrieve)

    original_select = svc.select

    async def select(**kwargs):
        spy.session.current_narrative_id = spy.anchor_narrative.id
        return await original_select(**kwargs)

    svc.select = select
    return svc, spy
